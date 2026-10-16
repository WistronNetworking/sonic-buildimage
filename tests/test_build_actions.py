import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import test_wistron_build_menu as fixtures

menu, tui = fixtures.menu, fixtures.tui
GNU_MAKE = shutil.which('make')


class BuildActionTests(unittest.TestCase):
    setUp = fixtures.BuildMenuTests.setUp
    git = fixtures.BuildMenuTests.git
    settings = fixtures.BuildMenuTests.settings
    execute = fixtures.BuildMenuTests.execute

    def prepared(self, flavor='amazon'):
        settings = self.settings(flavor)
        result = self.execute(settings)
        self.assertEqual(result['status'], 'success', result.get('error'))
        (settings.workspace / 'target' / menu.RFS).write_bytes(b'first-stage-rootfs')
        return settings

    def test_actions_stages_clean_and_cli_compatibility(self):
        settings = self.settings()
        self.assertEqual(settings.build_action, 'standard')
        self.assertEqual(len(menu.stages(settings)), 4)
        settings.mode = 'clean'
        settings.set_build_action('make')
        self.assertEqual(settings.mode, 'normal')
        self.assertEqual(settings.clean_display, 'N/A: retain outputs')
        self.assertEqual([s[2] for s in menu.stages(settings)], ['bookworm', 'target/' + menu.INSTALLER])
        settings.set_build_action('installer-only')
        self.assertEqual(len(menu.stages(settings)), 1)
        command = menu.make_command(settings, 'trixie', 'target/' + menu.INSTALLER)
        self.assertEqual(command[:4], ['make', '-f', 'Makefile.work', 'BLDENV=trixie'])
        settings.set_build_action('standard')
        self.assertEqual(settings.mode, 'normal')
        for action in ('make', 'installer-only'):
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                code = menu.main(['--source', str(self.source), '--state-dir', str(self.state),
                                  '--build-action', action, '--mode', 'clean', '--dry-run'])
            self.assertEqual(code, 1)
            self.assertIn('--mode clean is only supported', error.getvalue())
        self.assertFalse(self.state.exists())

    def test_incremental_preserves_workspace_edits_and_rebuilds_deleted_package(self):
        settings = self.prepared()
        workspace = settings.workspace
        local = workspace / 'uncommitted.py'
        local.write_text('edited_in_workspace = True\n')
        (self.source / 'uncommitted.py').write_text('original_repo_changed = True\n')
        (workspace / 'functions.sh').write_text('sonic_get_version() { echo Workspace.9; }\n')
        package = workspace / 'target/debs/bookworm/swss_1.0.0_arm64.deb'
        package.unlink()
        before = menu.inventory(self.source)['sha256']
        previous_calls = len((self.root / 'make.jsonl').read_text().splitlines())
        settings.set_build_action('make')
        with mock.patch.object(menu.Runner, 'prepare_workspace', side_effect=AssertionError('unexpected sync')), \
                mock.patch.object(menu.Runner, 'normalize_ztp', side_effect=AssertionError('unexpected patch')):
            result = self.execute(settings)
        self.assertEqual(result['status'], 'success', result.get('error'))
        self.assertEqual(local.read_text(), 'edited_in_workspace = True\n')
        self.assertTrue(package.exists())
        self.assertEqual(result['image_version'], 'Workspace.9')
        self.assertFalse(result['source_refresh'])
        self.assertEqual(result['effective_source'], str(workspace))
        self.assertEqual(menu.inventory(self.source)['sha256'], before)
        calls = [json.loads(line) for line in (self.root / 'make.jsonl').read_text().splitlines()[previous_calls:]]
        self.assertEqual([args[-1] for args in calls], ['bookworm', 'target/' + menu.INSTALLER])
        self.assertTrue(all('Makefile.work' in args for args in calls))
        for stage in result['stages']:
            self.assertEqual(stage['environment']['SONIC_OVERRIDE_BUILD_VARS'], '--old-file=.platform')
        self.assertTrue((Path(result['log']).parent / 'backup' / menu.INSTALLER).exists())

    def test_workspace_metadata_and_default_version_use_actual_build_tree(self):
        settings = self.prepared('wistron')
        self.git(settings.workspace, 'checkout', '-qb', 'workspace-branch')
        (settings.workspace / 'functions.sh').write_text('sonic_get_version() { echo Existing.7; }\n')
        settings.set_build_action('make')
        self.assertEqual(settings.git_branch, 'workspace-branch')
        self.assertEqual(settings.source_version, 'Existing.7')
        settings.set_build_action('installer-only')
        self.assertEqual(settings.source_version, 'Existing.7')
        settings.set_build_version('Custom.8')
        self.assertEqual(settings.version_display, 'Custom.8')
        settings.set_build_action('standard')
        self.assertEqual(settings.source_version, 'Fixture.1.0.0')

    def test_metadata_read_binds_one_source_while_action_changes(self):
        settings = self.prepared('wistron')
        (settings.workspace / 'functions.sh').write_text('sonic_get_version() { echo Workspace.9; }\n')
        original = menu.SourceQuery.__call__
        changed = []
        def switch_after_first_query(query, command, **kwargs):
            output = original(query, command, **kwargs)
            if not changed:
                changed.append(True)
                settings.set_build_action('make')
            return output
        with mock.patch.object(menu.SourceQuery, '__call__', switch_after_first_query):
            values = settings.read_source_metadata()
        self.assertEqual(settings.build_action, 'make')
        self.assertEqual(values['version'], 'Fixture.1.0.0')

    def test_existing_workspace_preconditions_fail_without_changes(self):
        settings = self.settings()
        settings.set_build_action('make')
        with self.assertRaisesRegex(menu.BuildError, 'Run Clean build first'):
            settings.validate()
        self.assertFalse(self.state.exists())
        settings = self.prepared('wistron')
        settings.set_build_action('make')
        (settings.workspace / '.arch').write_text('amd64\n')
        with self.assertRaisesRegex(menu.BuildError, 'configuration mismatch'):
            settings.validate()
        (settings.workspace / '.arch').write_text('arm64\n')
        marker = settings.workspace / menu.OWNER
        record = json.loads(marker.read_text())
        record['flavor'] = 'amazon'
        marker.write_text(json.dumps(record))
        with self.assertRaisesRegex(menu.BuildError, 'matching menu workspace'):
            settings.validate()

    def test_incremental_rejects_platform_changes_before_reusing_configuration(self):
        settings = self.prepared()
        settings.select_gui_action('make')
        platform = settings.workspace / '.platform'
        platform.write_text('generic\n')
        with self.assertRaisesRegex(menu.BuildError, 'configuration mismatch: .platform'):
            settings.validate()
        self.assertEqual(platform.read_text(), 'generic\n')

    def test_installer_only_both_customers_default_custom_and_frozen_inputs(self):
        for flavor in ('amazon', 'wistron'):
            settings = self.prepared(flavor)
            settings.set_build_action('installer-only')
            for version in ('', 'Custom.123'):
                with self.subTest(flavor=flavor, version=version):
                    settings.set_build_version(version)
                    package = settings.workspace / 'target/debs/trixie/swss_1.0.0_arm64.deb'
                    before = menu.digest_file(package)
                    with mock.patch.object(menu.Runner, 'prepare_workspace', side_effect=AssertionError('sync')), \
                            mock.patch.object(menu.Runner, 'normalize_ztp', side_effect=AssertionError('patch')):
                        result = self.execute(settings)
                    self.assertEqual(result['status'], 'success', result.get('error'))
                    self.assertEqual(result['image_version'], version or 'Fixture.1.0.0')
                    self.assertEqual(len(result['stages']), 1)
                    self.assertTrue(result['reused_artifacts_unchanged'])
                    self.assertEqual(menu.digest_file(package), before)
                    flags = result['stages'][0]['environment']['SONIC_OVERRIDE_BUILD_VARS']
                    self.assertIn('--always-make', flags)
                    self.assertIn('--old-file=target/' + menu.RFS, flags)
                    self.assertNotIn('SONIC_CONFIG_MAKE_JOBS', ' '.join(result['stages'][0]['command']))

    def test_missing_rootfs_and_input_mutation_fail_without_publication(self):
        settings = self.prepared()
        settings.set_build_action('installer-only')
        rootfs = settings.workspace / 'target' / menu.RFS
        rootfs.unlink()
        with self.assertRaisesRegex(menu.BuildError, 'requires existing rootfs'):
            settings.validate()
        rootfs.write_bytes(b'rootfs')
        command = menu.Runner.command
        def corrupt(runner, args, **kwargs):
            code = command(runner, args, **kwargs)
            if args[-1].endswith('.bin'):
                rootfs.write_bytes(b'changed')
            return code
        with mock.patch.object(menu.Runner, 'command', corrupt):
            result = self.execute(settings)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('changed a reused input', result['error'])
        self.assertNotIn('artifact', result)
        self.assertFalse(list(Path(result['log']).parent.glob('*.bin.sha256')))

    def test_new_action_dry_runs_do_not_change_workspace_or_build(self):
        settings = self.prepared('wistron')
        before = menu.inventory(settings.workspace)['sha256']
        calls = (self.root / 'make.jsonl').read_bytes()
        for action in ('make', 'installer-only'):
            output = io.StringIO()
            with mock.patch.object(menu, 'preview'), contextlib.redirect_stdout(output):
                code = menu.main(['--source', str(self.source), '--state-dir', str(self.state),
                                  '--build-action', action, '--flavor', 'wistron', '--dry-run'])
            self.assertEqual(code, 0)
            data = json.loads(output.getvalue())
            self.assertEqual(len(data['commands']), 2 if action == 'make' else 1)
            self.assertFalse(data['source_refresh'])
        self.assertEqual(menu.inventory(settings.workspace)['sha256'], before)
        self.assertEqual((self.root / 'make.jsonl').read_bytes(), calls)

    def test_numeric_action_picker_has_only_incremental_and_clean(self):
        settings = self.settings(mode='clean')
        output = io.StringIO()
        with mock.patch('builtins.input', side_effect=['3', '1', '3', '2', '3', '1', '0']), \
                contextlib.redirect_stdout(output):
            self.assertEqual(menu.menu(settings), 0)
        self.assertEqual(settings.build_action, 'make')
        self.assertEqual(settings.mode, 'normal')
        self.assertNotIn('clean rebuild', output.getvalue())
        self.assertNotIn('Standard build', output.getvalue())
        self.assertNotIn('Installer only', output.getvalue())
        self.assertIn('Clean build', output.getvalue())

    def test_clean_action_refreshes_source_and_backs_up_target(self):
        settings = self.prepared()
        (settings.workspace / 'uncommitted.py').write_text('workspace only\n')
        (self.source / 'uncommitted.py').write_text('clean source update\n')
        (settings.workspace / 'target/keep-existing').write_text('old output\n')
        settings.select_gui_action('clean')
        result = self.execute(settings)
        self.assertEqual(result['status'], 'success', result.get('error'))
        self.assertEqual(settings.action_display, 'Clean build')
        self.assertTrue(result['source_refresh'])
        self.assertEqual(len(result['stages']), 4)
        self.assertEqual((settings.workspace / 'uncommitted.py').read_text(), 'clean source update\n')
        self.assertFalse((settings.workspace / 'target/keep-existing').exists())
        self.assertTrue((Path(result['log']).parent / 'backup/target/keep-existing').is_file())


class NativeMakeGuardTests(unittest.TestCase):
    def test_incremental_only_ignores_platform_timestamp_and_keeps_other_dependencies(self):
        for scenario in ('normal-make', 'platform-only', 'missing-package', 'changed-source'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(prefix='wbm-platform-time-') as temp:
                root = Path(temp)
                rules = ('bin: docker.gz\n\t@echo bin >> rebuilt; touch $@\n'
                         'docker.gz: .platform a.deb b.deb\n\t@echo docker >> rebuilt; touch $@\n'
                         'a.deb: .platform a.c\n\t@echo a >> rebuilt; touch $@\n'
                         'b.deb: .platform b.c\n\t@echo b >> rebuilt; touch $@\n')
                (root / 'Makefile').write_text(rules)
                for name in ('.platform', 'a.c', 'b.c', 'a.deb', 'b.deb', 'docker.gz'):
                    path = root / name
                    path.write_text('existing ' + name)
                    os.utime(path, (1700000010 if name.endswith(('.deb', '.gz')) else 1700000000,) * 2)
                os.utime(root / '.platform', (1700000100,) * 2)
                stamp = (root / '.platform').stat().st_mtime_ns
                b_before = (root / 'b.deb').stat().st_mtime_ns
                if scenario == 'missing-package':
                    (root / 'a.deb').unlink()
                if scenario == 'changed-source':
                    os.utime(root / 'a.c', (1700000200,) * 2)
                flags = [] if scenario == 'normal-make' else ['--old-file=.platform']
                result = subprocess.run([GNU_MAKE, *flags, 'bin'], cwd=root, text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertEqual(result.returncode, 0, result.stdout)
                rebuilt = (root / 'rebuilt').read_text().splitlines()
                expected = ['a', 'b', 'docker', 'bin'] if scenario == 'normal-make' else (
                    ['bin'] if scenario == 'platform-only' else ['a', 'docker', 'bin'])
                self.assertEqual(rebuilt, expected)
                self.assertEqual((root / '.platform').stat().st_mtime_ns, stamp)
                if scenario != 'normal-make':
                    self.assertEqual((root / 'b.deb').stat().st_mtime_ns, b_before)

    def test_timestamp_override_is_scoped_to_incremental_and_does_not_freeze_packages(self):
        settings = menu.Settings(Path('/fixture/source'), Path('/fixture/state'))
        self.assertIsNone(menu.incremental_environment(settings))
        settings.select_gui_action('clean')
        self.assertIsNone(menu.incremental_environment(settings))
        settings.set_build_action('installer-only')
        self.assertIsNone(menu.incremental_environment(settings))
        settings.select_gui_action('make')
        override = menu.incremental_environment(settings)['SONIC_OVERRIDE_BUILD_VARS']
        self.assertEqual(override, '--old-file=.platform')
        self.assertNotIn('--always-make', override)
        self.assertNotIn('.deb', override)

    def test_percent_in_a_filename_does_not_whitelist_other_products(self):
        with tempfile.TemporaryDirectory(prefix='wbm-percent-guard-') as temp:
            root = Path(temp)
            controls = root / 'target/control'
            controls.mkdir(parents=True)
            frozen = 'target/package%3a.deb'
            missing = 'target/packageOTHER3a.deb'
            installer = 'target/' + menu.INSTALLER
            (root / frozen).write_text('existing')
            (controls / 'frozen.list').write_text(frozen)
            (controls / 'guard.mk').write_text(menu.installer_guard(Path('target/control')))
            (root / 'base.mk').write_text('SONIC_TARGET_LIST := ' + ' '.join([installer, frozen, missing]) + '\n'
                + installer + ': ' + frozen + ' ' + missing + '\n\t@touch $@\n'
                + frozen.replace('%', '\\%') + ':\n\t@echo COMPILE >> compiled\n'
                + missing + ':\n\t@echo COMPILE >> compiled; touch $@\n')
            result = subprocess.run([GNU_MAKE, '-f', 'base.mk', '-f', 'target/control/guard.mk',
                                     '--always-make', '--old-file=' + frozen, installer], cwd=root,
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(missing, result.stdout)
            self.assertFalse((root / 'compiled').exists(), result.stdout)

    def test_only_installer_is_rebuilt_and_missing_products_never_execute_recipes(self):
        for missing in (None, 'target/component.deb', 'target/tool.whl', 'target/docker-service.gz', 'target/rfs.squashfs'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory(prefix='wbm-native-make-') as temp:
                root = Path(temp)
                controls = root / 'target/control'
                controls.mkdir(parents=True)
                products = ['target/component.deb', 'target/tool.whl', 'target/docker-service.gz', 'target/rfs.squashfs']
                for name in products:
                    if name != missing:
                        (root / name).write_text('existing ' + name)
                installer = 'target/' + menu.INSTALLER
                (root / installer).write_text('old installer')
                frozen = [name for name in products if name != missing]
                (controls / 'frozen.list').write_text(' '.join(frozen))
                (controls / 'guard.mk').write_text(menu.installer_guard(Path('target/control')))
                (root / 'base.mk').write_text('SONIC_TARGET_LIST := ' + ' '.join(products + [installer, 'target/unused.deb']) + '\n'
                    '.PHONY: force\nforce:\n\t@true\n' + installer + ': ' + ' '.join(products) + '\n\t@echo NEW > $@\n'
                    + ' '.join(products + ['target/unused.deb']) + ': force\n\t@echo BUILD >> recipes-ran\n\t@touch $@\n')
                before = {name: menu.digest_file(root / name) for name in frozen}
                result = subprocess.run([GNU_MAKE, '-f', 'base.mk', '-f', 'target/control/guard.mk', '--always-make',
                                         *['--old-file=' + name for name in frozen], installer], cwd=root,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                self.assertFalse((root / 'recipes-ran').exists(), result.stdout)
                self.assertEqual({name: menu.digest_file(root / name) for name in frozen}, before)
                if missing:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(missing, result.stdout)
                    self.assertEqual((root / installer).read_text(), 'old installer')
                else:
                    self.assertEqual(result.returncode, 0, result.stdout)
                    self.assertEqual((root / installer).read_text(), 'NEW\n')

    def test_incremental_make_rebuilds_deleted_package_and_dependent_docker_and_bin(self):
        with tempfile.TemporaryDirectory(prefix='wbm-incremental-make-') as temp:
            root = Path(temp)
            (root / 'Makefile').write_text('bin: docker\n\t@echo bin >> rebuilt; touch $@\n'
                'docker: package.deb\n\t@echo docker >> rebuilt; touch $@\n'
                'package.deb: source.c\n\t@echo package >> rebuilt; touch $@\n')
            (root / 'source.c').write_text('source')
            subprocess.run([GNU_MAKE, 'bin'], cwd=root, check=True, stdout=subprocess.DEVNULL)
            (root / 'rebuilt').unlink()
            (root / 'package.deb').unlink()
            subprocess.run([GNU_MAKE, 'bin'], cwd=root, check=True, stdout=subprocess.DEVNULL)
            self.assertEqual((root / 'rebuilt').read_text().splitlines(), ['package', 'docker', 'bin'])


class ActionTuiTests(unittest.TestCase):
    def test_action_selector_only_clean_and_incremental(self):
        suite = fixtures.TuiTests()
        suite.setUp()
        suite.settings.mode = 'clean'
        screen = fixtures.TuiScreen([ord('3'), 10, ord('1'), 10,
                                    ord('3'), 10, ord('2'), 10, ord('0'), 10])
        with suite.terminal_environment():
            console = suite.console(screen)
            try:
                self.assertEqual(console.loop()[0], 'exit')
            finally:
                console.close()
        self.assertEqual(suite.settings.build_action, 'standard')
        self.assertEqual(suite.settings.mode, 'clean')
        frames = '\n'.join(screen.frames)
        self.assertIn('Incremental build', frames)
        self.assertIn('Clean build', frames)
        self.assertNotIn('clean rebuild', frames)
        self.assertNotIn('Standard build', frames)
        self.assertNotIn('Installer only', frames)

    def test_metadata_context_switch_discards_inflight_values_and_refreshes_immediately(self):
        settings = mock.Mock(arch='arm64', flavor='amazon', build_action='standard')
        def read(stop):
            action = settings.build_action
            if action == 'standard':
                stop.wait(0.06)
            return {'branch': action, 'commit': ('123', action), 'status': 'Clean', 'version': action}
        settings.read_source_metadata = read
        cache = tui.MetadataCache(settings, interval=60)
        self.addCleanup(cache.close)
        settings.build_action = 'make'
        values, _ = cache.snapshot()
        self.assertEqual(values['branch'], 'Loading...')
        deadline = time.monotonic() + 2
        while cache.snapshot()[0]['branch'] != 'make':
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        self.assertEqual(cache.refresh_now()['version'], 'make')

    def test_result_without_action_remains_standard_build(self):
        self.assertIn('Build action: Standard build', tui.result_lines({'status': 'success'}))
