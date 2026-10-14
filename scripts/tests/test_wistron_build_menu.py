import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


MODULE = Path(__file__).resolve().parents[1] / 'wistron_build_menu.py'
spec = importlib.util.spec_from_file_location('wistron_build_menu', MODULE)
menu = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = menu
spec.loader.exec_module(menu)
TUI_MODULE = MODULE.with_name('wistron_build_tui.py')
tui_spec = importlib.util.spec_from_file_location('wistron_build_tui', TUI_MODULE)
tui = importlib.util.module_from_spec(tui_spec)
sys.modules[tui_spec.name] = tui
tui_spec.loader.exec_module(tui)


DOCKER = r'''#!/usr/bin/env python3
import json, os, pathlib, shutil, sys
args = sys.argv[1:]
with open(os.environ['FAKE_DOCKER_LOG'], 'a') as stream:
    stream.write(json.dumps(args) + '\n')
if args[:2] == ['image', 'inspect']:
    if os.environ.get('MISSING_IMAGE'):
        sys.exit(1)
    print(json.dumps([{'Architecture':'arm64', 'Id':'sha256:' + args[2]}]))
elif args and args[0] == 'ps':
    pass
elif args and args[0] == 'run':
    mount = args[args.index('--mount') + 1]
    root = pathlib.Path(mount.split('src=',1)[1].split(',dst=',1)[0])
    relative = args[-1].removeprefix('/repo/')
    victim = root / relative
    assert victim.resolve().is_relative_to(root.resolve()) and victim != root
    shutil.rmtree(victim)
elif args and args[0] == 'stop':
    pass
else:
    raise RuntimeError('Unexpected Docker action: ' + repr(args))
'''

MAKE = r'''#!/usr/bin/env python3
import hashlib, io, json, os, pathlib, subprocess, sys, tarfile, time, zipfile
args = sys.argv[1:]
values = dict(a.split('=',1) for a in args if '=' in a)
target = args[-1]
with open(os.environ['FAKE_MAKE_LOG'], 'a') as stream:
    stream.write(json.dumps(args) + '\n')
print('fixture build stage ' + target, flush=True)
if os.environ.get('SLEEP_BUILD'):
    pathlib.Path(os.environ['BUILD_READY']).write_text('ready')
    time.sleep(60)
if os.environ.get('FAIL_TARGET') == target:
    sys.exit(23)
if target == 'configure':
    if values['ENABLE_ZTP'] == 'y':
        subprocess.run(['bash', './apply_patches.sh', '-z'], check=True)
    pathlib.Path('.arch').write_text('arm64\n')
    pathlib.Path('.platform').write_text('marvell-prestera\n')
else:
    distro = 'bookworm' if values['NOBOOKWORM'] == '0' else 'trixie'
    package = pathlib.Path('target/debs') / distro / 'swss_1.0.0_arm64.deb'
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_text('rebuilt\n')
    package.with_suffix('.deb.log').write_text('fixture component log\n')
    if target.endswith('.bin'):
        enabled = values['ENABLE_ZTP'] == 'y'
        status = 'Package: base-files\nStatus: install ok installed\nVersion: 1\n\n'
        if enabled:
            status += 'Package: sonic-ztp\nStatus: install ok installed\nVersion: 1.0.0\n\n'
        version = values.get('SONIC_IMAGE_VERSION') or subprocess.check_output(
            ['bash', '-c', '. ./functions.sh && sonic_get_version']).decode().strip()
        version = os.environ.get('FAKE_IMAGE_VERSION', version)
        content = json.dumps({'status':status, 'version': "build_version: '" + version + "'\n",
                              'paths': ['squashfs-root/lib/systemd/system/ztp.service'] if enabled else []})
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, 'w') as archive:
            archive.writestr('fs.squashfs', content)
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode='w') as archive:
            data = inner.getvalue()
            info = tarfile.TarInfo('installer/fs.zip')
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        data = payload.getvalue()
        header = '#!/bin/sh\npayload_image_size=' + str(len(data)) + '\npayload_sha1=' + hashlib.sha1(data).hexdigest() + '\nexit_marker\n'
        pathlib.Path(target).write_bytes(header.encode() + data)
'''

UNSQUASH = r'''#!/usr/bin/env python3
import json, pathlib, sys
content = json.loads(pathlib.Path(sys.argv[2]).read_text())
print(content['version'] if sys.argv[-1] == 'etc/sonic/sonic_version.yml' else
      content['status'] if sys.argv[1] == '-cat' else '\n'.join(content['paths']))
'''


class TerminalBuffer(io.StringIO):
    def isatty(self):
        return True


class BuildMenuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wbm-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.state = self.root / 'builds'
        self.git(self.source, 'init', '-q')
        self.git(self.source, 'config', 'user.name', 'Fixture')
        self.git(self.source, 'config', 'user.email', 'fixture@example.invalid')
        for name in menu.REQUIRED:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture\n')
        (self.source / 'functions.sh').write_text('sonic_get_version() { echo Fixture.1.0.0; }\n')
        for index, name in enumerate(menu.ZTP_PATCHES):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('diff --git a/src/sonic-ztp/feature.py b/src/sonic-ztp/feature.py\n'
                            '--- a/src/sonic-ztp/feature.py\n+++ b/src/sonic-ztp/feature.py\n'
                            f'@@ -1 +1 @@\n-level={index}\n+level={index+1}\n')
        patches = ' '.join(menu.ZTP_PATCHES)
        reverse = ' '.join(reversed(menu.ZTP_PATCHES))
        script = '#!/bin/bash\nset -e\n'
        script += f'if [[ "$1" == "ZTP=no" ]]; then\nfor p in {reverse}; do\n'
        script += 'if patch -p1 -R --force --dry-run < "$p" >/dev/null 2>&1; then patch -p1 -R --batch < "$p"; fi\ndone\n'
        script += 'elif ! grep -q "level=4" src/sonic-ztp/feature.py; then\n'
        script += f'for p in {patches}; do patch -p1 --batch --forward < "$p"; done\nfi\n'
        (self.source / 'apply_patches.sh').write_text(script)
        (self.source / '.gitignore').write_text('target/\n__pycache__/\nignored-private\n')
        self.git(self.source, 'add', '.')
        self.git(self.source, 'commit', '-qm', 'base')
        sub = self.source / 'src/sonic-ztp'
        sub.mkdir(parents=True)
        self.git(sub, 'init', '-q')
        self.git(sub, 'config', 'user.name', 'Fixture')
        self.git(sub, 'config', 'user.email', 'fixture@example.invalid')
        (sub / 'feature.py').write_text('level=0\n')
        self.git(sub, 'add', '.')
        self.git(sub, 'commit', '-qm', 'submodule base')
        (self.source / '.gitmodules').write_text('[submodule "ztp"]\n path = src/sonic-ztp\n url = ../ztp\n')
        self.git(self.source, 'add', '.gitmodules', 'src/sonic-ztp')
        self.git(self.source, 'commit', '-qm', 'submodule')
        # Existing applied ZTP stack and untracked submodule edit.
        (sub / 'feature.py').write_text('level=4\n')
        (sub / 'local.py').write_text('local_change = True\n')
        (self.source / 'uncommitted.py').write_text('user_change = True\n')
        (self.source / 'ignored-private').write_text('must not be copied\n')
        (self.source / 'target').mkdir()
        (self.source / 'target/keep').write_text('original target\n')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name, body in [('docker', DOCKER), ('make', MAKE), ('unsquashfs', UNSQUASH)]:
            path = self.bin / name
            path.write_text(body)
            path.chmod(0o755)
        self.environment = {'PATH':str(self.bin) + os.pathsep + os.environ['PATH'],
                            'FAKE_DOCKER_LOG':str(self.root / 'docker.jsonl'),
                            'FAKE_MAKE_LOG':str(self.root / 'make.jsonl')}
        self.patch_env = mock.patch.dict(os.environ, self.environment)
        self.patch_env.start()
        self.addCleanup(self.patch_env.stop)

    def git(self, root, *args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL)

    def settings(self, flavor='wistron', mode='normal'):
        return menu.Settings(self.source, self.state, flavor=flavor, mode=mode)

    def execute(self, settings):
        with contextlib.redirect_stdout(io.StringIO()):
            return menu.Runner(settings).execute()

    def test_isolated_versions_keep_dirty_source_and_submodule(self):
        source_hash = menu.inventory(self.source)['sha256']
        source_status = self.git(self.source, 'status', '--porcelain=v1', '-z')
        wistron = self.execute(self.settings())
        amazon = self.execute(self.settings('amazon'))
        self.assertEqual(wistron['status'], 'success')
        self.assertEqual(amazon['status'], 'success')
        self.assertFalse(wistron['ztp_validation']['sonic_ztp_installed'])
        self.assertTrue(amazon['ztp_validation']['sonic_ztp_installed'])
        self.assertNotEqual(wistron['workspace'], amazon['workspace'])
        self.assertEqual(menu.inventory(self.source)['sha256'], source_hash)
        self.assertEqual(self.git(self.source, 'status', '--porcelain=v1', '-z'), source_status)
        self.assertEqual((self.source / 'target/keep').read_text(), 'original target\n')
        for result in (wistron, amazon):
            self.assertEqual(result['jobs'], 8)
            self.assertEqual(result['make_jobs'], 'sonic-default')
            workspace = Path(result['workspace'])
            self.assertTrue((workspace / 'src/sonic-ztp/local.py').exists())
            self.assertTrue((workspace / 'uncommitted.py').exists())
            self.assertFalse((workspace / 'ignored-private').exists())
            self.assertTrue((workspace / '.git').is_dir())
            self.assertTrue((workspace / 'src/sonic-ztp/.git').is_dir())
            artifact = Path(result['artifact'])
            self.assertEqual(menu.digest_file(artifact), result['sha256'])
            self.assertTrue(result['slave_images_unchanged'])
            self.assertTrue((artifact.parent / 'component-logs/debs/bookworm/swss_1.0.0_arm64.deb.log').exists())
        for line in (self.root / 'make.jsonl').read_text().splitlines():
            args = json.loads(line)
            self.assertIn('SONIC_DPKG_CACHE_METHOD=none', args)
            self.assertIn('SONIC_VERSION_CACHE_METHOD=none', args)
            self.assertIn('SONIC_BUILD_JOBS=8', args)
            self.assertFalse(any(a.startswith('SONIC_CONFIG_MAKE_JOBS=') for a in args))
            self.assertFalse(any(a.startswith('SONIC_BUILD_VARS=') for a in args))
            self.assertIn('DOCKER_USER_BUILD=false', args)
            self.assertIn('SONIC_SLAVE_BASE_BUILD=:', args)
            self.assertEqual('PLATFORM=marvell-prestera' in args, args[-1] == 'configure')

    def test_normal_keeps_packages_clean_backs_up_target(self):
        first = self.execute(self.settings())
        workspace = Path(first['workspace'])
        sentinel = workspace / 'target/keep-generated'
        sentinel.write_text('keep this\n')
        second = self.execute(self.settings())
        self.assertEqual(second['status'], 'success')
        self.assertTrue(sentinel.exists())
        third = self.execute(self.settings(mode='clean'))
        self.assertEqual(third['status'], 'success')
        self.assertFalse(sentinel.exists())
        self.assertTrue((Path(third['log']).parent / 'backup/target/keep-generated').exists())

    def test_current_source_edits_and_deletions_are_synchronized(self):
        first = self.execute(self.settings())
        (self.source / 'uncommitted.py').unlink()
        (self.source / 'src/sonic-ztp/local.py').write_text('local_change = 2\n')
        second = self.execute(self.settings())
        self.assertEqual(second['status'], 'success')
        workspace = Path(first['workspace'])
        self.assertFalse((workspace / 'uncommitted.py').exists())
        self.assertEqual((workspace / 'src/sonic-ztp/local.py').read_text(), 'local_change = 2\n')

    def test_remote_branch_and_symbolic_refs_survive_snapshot(self):
        sub = self.source / 'src/sonic-ztp'
        tree = self.git(sub, 'rev-parse', 'HEAD^{tree}').decode().strip()
        remote_commit = self.git(sub, 'commit-tree', tree, '-p', 'HEAD', '-m', 'remote branch only').decode().strip()
        self.git(sub, 'update-ref', 'refs/remotes/origin/frr-10.4.1', remote_commit)
        self.git(sub, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/frr-10.4.1')
        self.git(sub, 'tag', '-a', 'release-fixture', remote_commit, '-m', 'annotated release')
        original = menu.inventory(self.source)['sha256']
        result = self.execute(self.settings())
        self.assertEqual(result['status'], 'success', result.get('error'))
        dest = Path(result['workspace']) / 'src/sonic-ztp'
        self.assertEqual(menu.repository_refs(dest), menu.repository_refs(sub))
        self.assertEqual(self.git(dest, 'rev-parse', 'origin/frr-10.4.1^{commit}').decode().strip(), remote_commit)
        self.git(dest, 'checkout', '-b', 'frr-10.4.1', 'origin/frr-10.4.1')
        self.assertEqual(menu.inventory(self.source)['sha256'], original)

    def test_refs_refresh_with_unchanged_head_and_build_branch(self):
        sub = self.source / 'src/sonic-ztp'
        head = self.git(sub, 'rev-parse', 'HEAD').decode().strip()
        self.git(sub, 'update-ref', 'refs/remotes/origin/current', head)
        self.git(sub, 'update-ref', 'refs/remotes/origin/removed', head)
        self.git(sub, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/current')
        self.git(sub, 'tag', 'removed-tag')
        first = self.execute(self.settings())
        self.assertEqual(first['status'], 'success', first.get('error'))
        dest = Path(first['workspace']) / 'src/sonic-ztp'
        self.git(dest, 'checkout', '-b', 'leftover-build-branch')
        tree = self.git(sub, 'rev-parse', 'HEAD^{tree}').decode().strip()
        updated = self.git(sub, 'commit-tree', tree, '-p', 'HEAD', '-m', 'updated remote').decode().strip()
        self.git(sub, 'update-ref', 'refs/remotes/origin/current', updated)
        self.git(sub, 'update-ref', '-d', 'refs/remotes/origin/removed')
        self.git(sub, 'tag', '-d', 'removed-tag')
        self.git(sub, 'tag', '-a', 'new-tag', updated, '-m', 'new release')
        self.assertEqual(self.git(sub, 'rev-parse', 'HEAD').decode().strip(), head)
        original = menu.inventory(self.source)['sha256']
        second = self.execute(self.settings())
        self.assertEqual(second['status'], 'success', second.get('error'))
        self.assertEqual(menu.repository_refs(dest), menu.repository_refs(sub))
        self.assertEqual(self.git(dest, 'rev-parse', '--symbolic-full-name', 'HEAD'),
                         self.git(sub, 'rev-parse', '--symbolic-full-name', 'HEAD'))
        self.assertEqual(menu.inventory(self.source)['sha256'], original)

    def test_detached_head_update_copies_commit_without_branch_ref(self):
        first = self.execute(self.settings())
        self.assertEqual(first['status'], 'success', first.get('error'))
        sub = self.source / 'src/sonic-ztp'
        refs_before = menu.repository_refs(sub)
        tree = self.git(sub, 'rev-parse', 'HEAD^{tree}').decode().strip()
        detached = self.git(sub, 'commit-tree', tree, '-p', 'HEAD', '-m', 'detached source update').decode().strip()
        self.git(sub, 'checkout', '--detach', detached)
        self.assertEqual(menu.repository_refs(sub), refs_before)
        original = menu.inventory(self.source)['sha256']
        second = self.execute(self.settings())
        self.assertEqual(second['status'], 'success', second.get('error'))
        dest = Path(second['workspace']) / 'src/sonic-ztp'
        self.assertEqual(self.git(dest, 'rev-parse', 'HEAD').decode().strip(), detached)
        self.assertEqual(self.git(dest, 'rev-parse', '--symbolic-full-name', 'HEAD').decode().strip(), 'HEAD')
        self.assertEqual(menu.inventory(self.source)['sha256'], original)

    def test_dry_run_missing_image_and_x86_never_build(self):
        args = ['--source', str(self.source), '--state-dir', str(self.state), '--dry-run']
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(menu.main(args), 0)
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root / 'make.jsonl').exists())
        with mock.patch.dict(os.environ, {'MISSING_IMAGE':'1'}), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(menu.main(args), 1)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(menu.main(args + ['--arch', 'amd64']), 1)
        self.assertFalse(self.state.exists())

    def test_failure_retains_real_exit_code_and_original_source(self):
        original = menu.inventory(self.source)['sha256']
        with mock.patch.dict(os.environ, {'FAIL_TARGET':'bookworm'}):
            result = self.execute(self.settings())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['stages'][-1]['exit_code'], 23)
        self.assertTrue(Path(result['log']).exists())
        self.assertEqual(menu.inventory(self.source)['sha256'], original)

    def test_ztp_conflict_stops_before_make(self):
        (self.source / 'src/sonic-ztp/feature.py').write_text('custom user code\n')
        result = self.execute(self.settings())
        self.assertEqual(result['status'], 'failed')
        self.assertFalse((self.root / 'make.jsonl').exists())
        self.assertEqual((self.source / 'src/sonic-ztp/feature.py').read_text(), 'custom user code\n')

    def test_wistron_artifact_rejects_ztp_service_without_package(self):
        status = 'Package: base-files\nStatus: install ok installed\nVersion: 1\n\n'
        with self.assertRaises(menu.BuildError):
            menu.check_ztp_status(status, 'squashfs-root/etc/systemd/system/ztp.service', False)

    def test_external_source_symlink_is_rejected(self):
        (self.source / 'outside-link').symlink_to(self.root / 'private')
        with self.assertRaises(menu.BuildError):
            menu.inventory(self.source)

    def test_tracked_runtime_symlink_is_preserved_without_copying_target(self):
        outside = self.root / 'runtime-data'
        outside.write_text('runtime data must not be copied')
        (self.source / 'runtime-link').symlink_to(outside)
        self.git(self.source, 'add', 'runtime-link')
        result = self.execute(self.settings())
        self.assertEqual(result['status'], 'success')
        target = Path(result['workspace']) / 'runtime-link'
        self.assertTrue(target.is_symlink())
        self.assertEqual(os.readlink(target), str(outside))
        self.assertFalse((Path(result['workspace']) / 'runtime-data').exists())

    def test_container_source_link_is_preserved(self):
        (self.source / 'container-link').symlink_to('/sonic/uncommitted.py')
        result = self.execute(self.settings())
        self.assertEqual(result['status'], 'success')
        self.assertEqual(os.readlink(Path(result['workspace']) / 'container-link'), '/sonic/uncommitted.py')

    def test_inherited_git_redirects_do_not_modify_source_index(self):
        self.git(self.source, 'add', 'uncommitted.py')
        before = self.git(self.source, 'diff', '--cached', '--binary')
        with mock.patch.dict(os.environ, {'GIT_DIR':str(self.source / '.git'),
                                         'GIT_WORK_TREE':str(self.source),
                                         'GIT_INDEX_FILE':str(self.source / '.git/index')}):
            result = self.execute(self.settings())
        self.assertEqual(result['status'], 'success')
        self.assertEqual(self.git(self.source, 'diff', '--cached', '--binary'), before)

    def test_inert_yang_definitions_do_not_count_as_runtime_ztp(self):
        status = 'Package: base-files\nStatus: install ok installed\nVersion: 1\n\n'
        result = menu.check_ztp_status(status, 'squashfs-root/usr/local/yang-models/sonic-ztp.yang', False)
        self.assertFalse(result['sonic_ztp_installed'])
        self.assertFalse(result['ztp_paths'])
        self.assertEqual(len(result['non_runtime_definitions']), 1)

    def test_foreign_workspace_and_nested_state_are_rejected(self):
        settings = self.settings()
        settings.workspace.mkdir(parents=True)
        (settings.workspace / 'private').write_text('do not touch')
        with self.assertRaises(menu.BuildError):
            self.execute(settings)
        menu.atomic_json(self.state / menu.OWNER,
                         {'schema':menu.SCHEMA, 'source':str(self.source)})
        result = self.execute(settings)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual((settings.workspace / 'private').read_text(), 'do not touch')
        with self.assertRaises(menu.BuildError):
            menu.Settings(self.source, self.source / 'builds').validate()

    def test_menu_numeric_selection_jobs_and_x86_placeholder(self):
        settings = self.settings()
        answers = ['1','2','6','4','3','0']
        with mock.patch('builtins.input', side_effect=answers) as prompts, mock.patch.object(menu.Runner, 'execute') as build:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(menu.menu(settings), 0)
        self.assertEqual(settings.arch, 'amd64')
        self.assertEqual(settings.jobs, 3)
        prompts.assert_any_call('Build job (SONIC_BUILD_JOBS, default 8): ')
        self.assertEqual(prompts.call_count, len(answers))
        build.assert_not_called()

    def test_cli_jobs_option_keeps_sonic_compile_default(self):
        args = ['--source', str(self.source), '--state-dir', str(self.state),
                '--dry-run', '--jobs', '3']
        output = io.StringIO()
        with mock.patch.object(menu, 'preview'), contextlib.redirect_stdout(output):
            self.assertEqual(menu.main(args), 0)
        for command in json.loads(output.getvalue())['commands']:
            self.assertIn('SONIC_BUILD_JOBS=3', command)
            self.assertFalse(any(a.startswith('SONIC_CONFIG_MAKE_JOBS=') for a in command))
            self.assertFalse(any(a.startswith('SONIC_BUILD_VARS=') for a in command))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            menu.main(args + ['--make-jobs', '4'])
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root / 'make.jsonl').exists())

    def test_build_version_auto_custom_cli_and_invalid_values(self):
        settings = self.settings()
        self.assertEqual(settings.source_version, 'Fixture.1.0.0')
        self.assertEqual(settings.version_display, 'Default: Fixture.1.0.0')
        for version in ('', 'Wistron_M.1.0.3'):
            output = io.StringIO()
            args = ['--source', str(self.source), '--state-dir', str(self.state),
                    '--dry-run', '--build-version', version]
            with mock.patch.object(menu, 'preview'), contextlib.redirect_stdout(output):
                self.assertEqual(menu.main(args), 0)
            for command in json.loads(output.getvalue())['commands']:
                self.assertIn('SONIC_IMAGE_VERSION=' + version, command)
        for version in ('bad version', 'bad/version', '$(id)', 'x;y', '-1', 'x' * 129):
            with self.subTest(version=version), self.assertRaises(menu.BuildError):
                settings.set_build_version(version)
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root / 'make.jsonl').exists())

    def test_custom_build_version_reaches_both_customer_installers_without_editing_functions(self):
        functions = (self.source / 'functions.sh').read_bytes()
        for customer in ('amazon', 'wistron'):
            settings = self.settings(customer)
            settings.set_build_version('Wistron_M.1.0.3')
            result = self.execute(settings)
            self.assertEqual(result['status'], 'success', result.get('error'))
            self.assertEqual(result['requested_image_version'], 'Wistron_M.1.0.3')
            self.assertEqual(result['image_version'], 'Wistron_M.1.0.3')
            for stage in result['stages']:
                self.assertIn('SONIC_IMAGE_VERSION=Wistron_M.1.0.3', stage['command'])
            self.assertEqual((self.source / 'functions.sh').read_bytes(), functions)
            self.assertEqual((Path(result['workspace']) / 'functions.sh').read_bytes(), functions)

    def test_wrong_installer_version_stops_success(self):
        settings = self.settings()
        settings.set_build_version('Wistron_M.1.0.3')
        with mock.patch.dict(os.environ, {'FAKE_IMAGE_VERSION':'Wrong.1.0.0'}):
            result = self.execute(settings)
        self.assertEqual(result['status'], 'failed')
        self.assertIn("does not match 'Wistron_M.1.0.3'", result['error'])
        self.assertTrue(Path(result['log']).is_file())
        self.assertTrue(result['slave_images_unchanged'])

    def test_menu_jobs_rejects_zero_and_enter_restores_eight(self):
        settings = self.settings()
        settings.jobs = 3
        output = io.StringIO()
        with mock.patch('builtins.input', side_effect=['4', '-1', '4', 'abc', '4', '0', '4', '', '0']):
            with contextlib.redirect_stdout(output):
                self.assertEqual(menu.menu(settings), 0)
        self.assertEqual(settings.jobs, 8)
        self.assertIn('Build job must be a positive integer.', output.getvalue())
        self.assertIn('Cannot run:', output.getvalue())
        self.assertNotIn('SONIC_CONFIG_MAKE_JOBS', output.getvalue())
        self.assertNotIn('nproc', output.getvalue())

    def test_numeric_version_choices_custom_default_and_cancel(self):
        settings = self.settings()
        output = io.StringIO()
        with mock.patch('builtins.input', side_effect=['5', '2', 'Custom.2', '5', '0', '0']) as prompts:
            with contextlib.redirect_stdout(output):
                self.assertEqual(menu.menu(settings), 0)
        self.assertEqual(settings.build_version, 'Custom.2')
        prompts.assert_any_call('1. Default: Fixture.1.0.0\n2. Custom: enter a version\n0. back\nOption: ')
        with mock.patch('builtins.input', side_effect=['5', '9', '5', '', '5', '1', '0']):
            with contextlib.redirect_stdout(output):
                self.assertEqual(menu.menu(settings), 0)
        self.assertEqual(settings.build_version, '')
        self.assertIn('Enter 1, 2, or 0.', output.getvalue())
        self.assertFalse(self.state.exists())

    def test_default_version_follows_updated_source_functions_without_modifying_it(self):
        settings = self.settings()
        settings.set_build_version('Custom.1')
        functions = self.source / 'functions.sh'
        functions.write_text('sonic_get_version() { echo Pulled.2.0.0; }\n')
        before = functions.read_bytes()
        with mock.patch('builtins.input', side_effect=['5', '1', '0']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(menu.menu(settings), 0)
        self.assertEqual(settings.build_version, '')
        self.assertEqual(settings.version_display, 'Default: Pulled.2.0.0')
        self.assertEqual(functions.read_bytes(), before)
        for distro in ('bookworm', 'trixie'):
            for target in ('configure', 'target/' + menu.INSTALLER):
                self.assertIn('SONIC_IMAGE_VERSION=', menu.make_command(settings, distro, target))

    def test_menu_and_submenu_options_are_on_separate_lines(self):
        settings = self.settings()
        output = io.StringIO()
        with mock.patch('builtins.input', side_effect=['1', '1', '2', '2', '0']) as prompts:
            with contextlib.redirect_stdout(output):
                self.assertEqual(menu.menu(settings), 0)
        expected = ('1. architecture\n2. customer\n3. clean rebuild\n4. build job\n'
                    '5. build version\n6. start build\n7. last result\n0. exit')
        self.assertIn(expected, output.getvalue())
        prompts.assert_any_call('1. ARM64\n2. X86: AMD64 (not supported yet)\nOption: ')
        prompts.assert_any_call('1. Amazon (ZTP)\n2. Wistron (no ZTP)\nOption: ')
        self.assertEqual(settings.arch, 'arm64')
        self.assertEqual(settings.flavor, 'wistron')

    def test_clean_rebuild_accepts_yes_no_and_y_n_and_preserves_current_on_enter(self):
        cases = [('normal', 'y', 'clean'), ('normal', ' Y ', 'clean'),
                 ('clean', 'n', 'normal'), ('clean', ' N ', 'normal'),
                 ('normal', 'yes', 'clean'), ('normal', ' YeS ', 'clean'),
                 ('clean', 'no', 'normal'), ('clean', ' NO ', 'normal'),
                 ('clean', '', 'clean'), ('normal', '  ', 'normal'),
                 ('clean', 'invalid', 'clean'), ('normal', '1', 'normal')]
        for initial, value, expected in cases:
            with self.subTest(initial=initial, value=value):
                settings = self.settings(mode=initial)
                output = io.StringIO()
                with mock.patch('builtins.input', side_effect=['3', value, '0']) as prompts:
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(menu.menu(settings), 0)
                self.assertEqual(settings.mode, expected)
                prompts.assert_any_call('Clean rebuild (Yes/No) [' + ('Yes' if initial == 'clean' else 'No') + ']: ')
                if value in ('invalid', '1'):
                    self.assertIn('Enter Yes or No.', output.getvalue())

    def test_summary_shows_customer_clean_flag_and_build_jobs(self):
        for flavor in ('amazon', 'wistron'):
            for mode in ('clean', 'normal'):
                with self.subTest(flavor=flavor, mode=mode):
                    settings = self.settings(flavor=flavor, mode=mode)
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        menu.preview(settings)
                    text = output.getvalue()
                    self.assertIn('Architecture: ARM64: Marvell Prestera', text)
                    self.assertIn('Customer: ' + flavor.title(), text.splitlines())
                    self.assertIn('Clean rebuild: ' + ('Yes: back up target and rebuild' if mode == 'clean'
                                                      else 'No: retain outputs, no caches'), text.splitlines())
                    self.assertIn('Build job (SONIC_BUILD_JOBS): 8', text)
                    self.assertNotIn('SONIC_CONFIG_MAKE_JOBS', text)
                    self.assertNotIn('nproc', text)

    def test_git_branch_display_uses_source_and_handles_detached_or_missing_repository(self):
        settings = self.settings()
        self.git(self.source, 'checkout', '-qb', 'menu-info')
        self.assertEqual(settings.git_branch, 'menu-info')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            menu.preview(settings)
        self.assertIn('Git branch: menu-info', output.getvalue().splitlines())
        commit = self.git(self.source, 'rev-parse', '--short', 'HEAD').decode().strip()
        self.assertEqual(settings.git_commit, commit)
        self.assertEqual(settings.git_commit_info, (commit, 'submodule'))
        self.assertIn('Git commit: ' + commit + ' submodule', output.getvalue().splitlines())
        self.assertIn('Source status: Modified', output.getvalue().splitlines())
        self.git(self.source, 'checkout', '-qb', 'another-source-branch')
        self.assertEqual(settings.git_branch, 'another-source-branch')
        self.git(self.source, 'checkout', '-q', '--detach', 'HEAD')
        commit = self.git(self.source, 'rev-parse', '--short', 'HEAD').decode().strip()
        self.assertEqual(settings.git_branch, 'detached HEAD (' + commit + ')')
        settings.source = self.root
        self.assertEqual(settings.git_branch, 'unavailable')
        self.assertEqual(settings.git_commit, 'unavailable')
        self.assertEqual(settings.git_commit_display, 'unavailable')
        self.assertEqual(settings.source_status, 'unavailable')

    def test_source_status_includes_untracked_files_and_submodule_edits_but_not_ignored_outputs(self):
        settings = self.settings()
        self.assertEqual(settings.source_status, 'Modified')
        (self.source / 'uncommitted.py').unlink()
        (self.source / 'src/sonic-ztp/local.py').unlink()
        (self.source / 'src/sonic-ztp/feature.py').write_text('level=0\n')
        self.assertEqual(settings.source_status, 'Clean')
        untracked = self.source / 'new-source.py'
        untracked.write_text('new_source = True\n')
        self.assertEqual(settings.source_status, 'Modified')
        self.git(self.source, 'add', 'new-source.py')
        self.assertEqual(settings.source_status, 'Modified')
        self.git(self.source, 'commit', '-qm', 'fixture source')
        self.assertEqual(settings.source_status, 'Clean')
        (self.source / 'src/sonic-ztp/feature.py').write_text('level=1\n')
        self.assertEqual(settings.source_status, 'Modified')

    def test_owned_messages_readme_help_and_results_are_english(self):
        self.assertNotRegex(MODULE.read_text(), r'[\u4e00-\u9fff]')
        self.assertNotRegex((MODULE.parents[1] / 'README.build-menu.md').read_text(), r'[\u4e00-\u9fff]')
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit_code:
            menu.main(['--help'])
        self.assertEqual(exit_code.exception.code, 0)
        self.assertIn('Wistron SONiC WSL build console', output.getvalue())
        self.assertNotRegex(TUI_MODULE.read_text(), r'[\u4e00-\u9fff]')
        self.assertNotIn('SONIC_CONFIG_MAKE_JOBS', output.getvalue())
        for status, label in [('success', 'Success'), ('failed', 'Failed'), ('interrupted', 'Interrupted')]:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                menu.show_result({'status': status, 'error': 'fixture error',
                                  'artifact': '/fixture/image.bin', 'image_bytes': 42,
                                  'sha256': 'fixture-sha256', 'log': '/fixture/build.log',
                                  'slave_images_unchanged': True})
            self.assertIn('Build result: ' + label, output.getvalue())
            self.assertIn('Full log:', output.getvalue())
            self.assertIn('Component logs:', output.getvalue())
            self.assertIn('Slave image IDs unchanged:', output.getvalue())
            self.assertNotRegex(output.getvalue(), r'[\u4e00-\u9fff]')

    def test_upstream_command_output_is_preserved(self):
        runner = menu.Runner(self.settings())
        runner.log = io.BytesIO()
        output = io.StringIO()
        payload = b'upstream: \x1b[35m\xe4\xb8\xad\xe6\x96\x87\x1b[0m\n'
        command = [sys.executable, '-c', 'import sys; sys.stdout.buffer.write(' + repr(payload) + ')']
        with contextlib.redirect_stdout(output):
            self.assertEqual(runner.command(command), 0)
        self.assertEqual(runner.log.getvalue(), payload)
        self.assertEqual(output.getvalue(), payload.decode())

    def test_color_policy_for_tty_non_tty_no_color_and_dumb_term(self):
        terminal = TerminalBuffer()
        with mock.patch.dict(os.environ, {'TERM': 'xterm-256color'}, clear=True):
            self.assertEqual(menu.terminal_text('Ready', 'cyan', stream=terminal), '\x1b[36mReady\x1b[0m')
            self.assertEqual(menu.terminal_text('Ready', bold=True, stream=terminal), '\x1b[1mReady\x1b[0m')
            self.assertEqual(menu.terminal_text('Ready', 'red', stream=io.StringIO()), 'Ready')
        for environment in ({'TERM': 'dumb'}, {'TERM': 'xterm', 'NO_COLOR': ''},
                            {'TERM': 'xterm', 'NO_COLOR': '1'}):
            with self.subTest(environment=environment):
                with mock.patch.dict(os.environ, environment, clear=True):
                    self.assertEqual(menu.terminal_text('Ready', 'red', bold=True, stream=terminal), 'Ready')

    def test_colored_menu_keeps_layout_and_resets_before_input(self):
        settings = self.settings()
        output = TerminalBuffer()
        with mock.patch.dict(os.environ, {'TERM': 'xterm'}, clear=True):
            with mock.patch('builtins.input', side_effect=['1', '2', '2', '2', '0']) as prompts:
                with contextlib.redirect_stdout(output):
                    self.assertEqual(menu.menu(settings), 0)
        raw = output.getvalue()
        text = re.sub(r'\x1b\[[0-9;]*m', '', raw)
        self.assertIn('1. architecture\n2. customer\n3. clean rebuild\n4. build job\n'
                      '5. build version\n6. start build\n7. last result\n0. exit', text)
        self.assertIn('\x1b[36m1.\x1b[0m architecture', raw)
        self.assertIn('\x1b[36m6.\x1b[0m start build', raw)
        self.assertNotIn('\x1b[32mstart build\x1b[0m', raw)
        self.assertIn('\x1b[1mX86: AMD64 (not supported yet)\x1b[0m', raw)
        self.assertIn('\x1b[1mWistron\x1b[0m', raw)
        for call in prompts.call_args_list:
            self.assertTrue(call.args[0].endswith('\x1b[0m'))

    def test_result_colors_and_default_foreground_paths_and_checksums(self):
        for status, label, code in [('success', 'Success', '32'), ('failed', 'Failed', '31'),
                                    ('interrupted', 'Interrupted', '33')]:
            for unchanged in (True, False):
                with self.subTest(status=status, unchanged=unchanged):
                    output = TerminalBuffer()
                    with mock.patch.dict(os.environ, {'TERM': 'xterm'}, clear=True):
                        with contextlib.redirect_stdout(output):
                            menu.show_result({'status': status, 'error': 'fixture error',
                                              'artifact': '/fixture/image.bin', 'image_bytes': 42,
                                              'sha256': 'fixture-sha256', 'log': '/fixture/build.log',
                                              'slave_images_unchanged': unchanged})
                    raw = output.getvalue()
                    self.assertIn('\x1b[1;' + code + 'm' + label + '\x1b[0m', raw)
                    self.assertIn('\x1b[1;' + ('32' if unchanged else '31') + 'm'
                                  + str(unchanged) + '\x1b[0m', raw)
                    self.assertIn('\x1b[36mInstaller:\x1b[0m /fixture/image.bin\n', raw)
                    self.assertIn('\x1b[36mSHA-256:\x1b[0m fixture-sha256\n', raw)
                    self.assertIn('\x1b[36mFull log:\x1b[0m /fixture/build.log\n', raw)

    def test_colored_build_ui_does_not_pollute_saved_logs_json_or_checksum(self):
        environment = dict(os.environ, TERM='xterm-256color')
        environment.pop('NO_COLOR', None)
        output = TerminalBuffer()
        with mock.patch.dict(os.environ, environment, clear=True):
            with contextlib.redirect_stdout(output):
                result = menu.Runner(self.settings()).execute()
        self.assertEqual(result['status'], 'success', result.get('error'))
        self.assertIn('\x1b[36m', output.getvalue())
        self.assertIn('\x1b[1;32mSuccess\x1b[0m', output.getvalue())
        log = Path(result['log'])
        self.assertNotIn(b'\x1b', log.read_bytes())
        self.assertNotIn(b'\\u001b', (log.parent / 'result.json').read_bytes().lower())
        self.assertNotIn(b'\\u001b', (self.state / 'last-run.json').read_bytes().lower())
        self.assertNotIn(b'\x1b', Path(result['artifact']).with_suffix('.bin.sha256').read_bytes())

    def test_error_colors_follow_stderr_tty_independently(self):
        args = ['--source', str(self.source), '--state-dir', str(self.state), '--arch', 'amd64', '--dry-run']
        for stderr_is_tty in (True, False):
            with self.subTest(stderr_is_tty=stderr_is_tty):
                stderr = TerminalBuffer() if stderr_is_tty else io.StringIO()
                stdout = io.StringIO() if stderr_is_tty else TerminalBuffer()
                with mock.patch.dict(os.environ, {'TERM': 'xterm'}, clear=True):
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        self.assertEqual(menu.main(args), 1)
                self.assertEqual('\x1b[31m' in stderr.getvalue(), stderr_is_tty)
                self.assertEqual(stderr.getvalue().endswith('\x1b[0m\n'), stderr_is_tty)

    def test_sigint_stops_build_process_and_records_interrupted(self):
        ready = self.root / 'ready'
        environment = dict(os.environ, SLEEP_BUILD='1', BUILD_READY=str(ready))
        command = [sys.executable, str(MODULE), '--source', str(self.source), '--state-dir', str(self.state),
                   '--flavor', 'wistron', '--mode', 'normal', '--run', '--yes']
        process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 15
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(ready.exists())
            process.send_signal(signal.SIGINT)
            output, _ = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 130, output.decode(errors='replace'))
            result = json.loads((self.state / 'last-run.json').read_text())
            self.assertEqual(result['status'], 'interrupted')
            self.assertEqual((self.source / 'src/sonic-ztp/feature.py').read_text(), 'level=4\n')
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


class TuiScreen:
    def __init__(self, keys=(), height=32, width=110):
        self.keys = list(keys)
        self.height, self.width = height, width
        self.frames = []
        self.erase()

    def erase(self):
        self.grid = [[' '] * self.width for _ in range(self.height)]
        self.styles = [[0] * self.width for _ in range(self.height)]

    def getmaxyx(self):
        return self.height, self.width

    def keypad(self, enabled):
        pass

    def timeout(self, timeout):
        pass

    def addstr(self, y, x, value, attr=0):
        assert 0 <= y < self.height and 0 <= x < self.width
        assert x + tui.cells(value) <= self.width
        for char in value:
            size = tui.cells(char)
            if not size:
                continue
            self.grid[y][x] = char
            self.styles[y][x] = attr
            for extra in range(1, size):
                self.grid[y][x + extra] = ' '
                self.styles[y][x + extra] = attr
            x += size

    def move(self, y, x):
        assert 0 <= y < self.height and 0 <= x < self.width

    def refresh(self):
        self.frames.append('\n'.join(''.join(row).rstrip() for row in self.grid))

    def getch(self):
        assert self.keys, 'TUI consumed more input than expected'
        key = self.keys.pop(0)
        if isinstance(key, BaseException):
            raise key
        if callable(key):
            return key(self)
        return key


class StaticMetadata:
    """Deterministic snapshots for layout and key-handling regression tests."""

    def __init__(self, settings):
        self.settings = settings
        self.values = self.read()
        self.revision = 0
        self.closed = False

    def read(self):
        return {'branch': self.settings.git_branch, 'commit': self.settings.git_commit_info,
                'status': self.settings.source_status, 'version': self.settings.source_version}

    def snapshot(self):
        return dict(self.values), self.revision

    def refresh_now(self):
        self.values = self.read()
        self.revision += 1
        return dict(self.values)

    def close(self):
        self.closed = True


class MetadataTests(unittest.TestCase):
    def wait_for(self, condition):
        deadline = time.monotonic() + 2
        while not condition():
            self.assertLess(time.monotonic(), deadline, 'Metadata worker did not finish')
            time.sleep(0.005)

    def test_initial_loading_partial_failure_and_last_valid_values(self):
        started, release = threading.Event(), threading.Event()
        values = {'branch': 'dev', 'commit': ('abc', 'title'), 'status': None, 'version': 'V.1'}
        def load(stop):
            started.set()
            while not release.wait(0.005):
                if stop.is_set():
                    return dict.fromkeys(values)
            return dict(values)
        settings = mock.Mock(read_source_metadata=load)
        cache = tui.MetadataCache(settings, interval=60)
        self.addCleanup(cache.close)
        self.assertTrue(started.wait(1))
        self.assertEqual(cache.snapshot()[0], tui.metadata_defaults('Loading...'))
        release.set()
        self.wait_for(lambda: cache.snapshot()[1] == 1)
        self.assertEqual(cache.snapshot()[0]['status'], 'unavailable')
        values.update(branch=None, commit=None, status='Clean', version=None)
        fresh = cache.refresh_now()
        self.assertEqual(fresh['branch'], 'unavailable')
        self.assertEqual(cache.snapshot()[0], {'branch': 'dev', 'commit': ('abc', 'title'),
                                            'status': 'Clean', 'version': 'V.1'})

    def test_periodic_reads_do_not_overlap_and_identical_values_do_not_redraw(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def load(stop):
            calls.append(threading.current_thread().name)
            started.set()
            while not release.wait(0.005):
                if stop.is_set():
                    break
            return tui.metadata_defaults('value')
        cache = tui.MetadataCache(mock.Mock(read_source_metadata=load), interval=0.02)
        self.addCleanup(cache.close)
        self.assertTrue(started.wait(1))
        time.sleep(0.05)
        self.assertEqual(len(calls), 1)
        release.set()
        self.wait_for(lambda: len(calls) >= 3)
        self.assertEqual(cache.snapshot()[1], 1)
        self.assertEqual(set(calls), {'build-menu-metadata'})

    def test_fresh_request_runs_after_inflight_read_and_shutdown_cancels_it(self):
        started, release = threading.Event(), threading.Event()
        count = 0
        def load(stop):
            nonlocal count
            count += 1
            started.set()
            while not release.wait(0.005):
                if stop.is_set():
                    return dict.fromkeys(tui.metadata_defaults(''))
            return tui.metadata_defaults(str(count))
        cache = tui.MetadataCache(mock.Mock(read_source_metadata=load), interval=60)
        self.addCleanup(cache.close)
        self.assertTrue(started.wait(1))
        result = []
        reader = threading.Thread(target=lambda: result.append(cache.refresh_now()))
        reader.start()
        self.wait_for(lambda: bool(cache.requests))
        release.set()
        reader.join(1)
        self.assertFalse(reader.is_alive())
        self.assertEqual(result[0]['version'], '2')
        cache.close()
        self.assertFalse(cache.thread.is_alive())
        with self.assertRaisesRegex(RuntimeError, 'stopped'):
            cache.refresh_now()
        release.clear()
        cache = tui.MetadataCache(mock.Mock(read_source_metadata=load), interval=60)
        self.assertTrue(started.wait(1))
        cache.close()
        self.assertFalse(cache.thread.is_alive())

    def test_query_timeout_and_cancel_reap_owned_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / 'pid'
            script = ('import os, pathlib, signal, subprocess, sys, time; '
                      'child=subprocess.Popen([sys.executable,"-c",'
                      '"import time; time.sleep(60)"]); '
                      'pathlib.Path(sys.argv[1]).write_text(str(child.pid)); '
                      'time.sleep(60)')
            with self.assertRaisesRegex(menu.BuildError, 'timed out'):
                menu.SourceQuery(timeout=0.15)([sys.executable, '-c', script, str(pid_file)])
            child = int(pid_file.read_text())
            self.assert_process_stopped(child)
            stop = threading.Event()
            query = menu.SourceQuery(stop)
            errors = []
            def run():
                try:
                    query([sys.executable, '-c', script, str(pid_file)])
                except menu.SourceQueryCancelled:
                    errors.append('cancelled')
            pid_file.unlink()
            worker = threading.Thread(target=run)
            worker.start()
            self.wait_for(pid_file.exists)
            child = int(pid_file.read_text())
            stop.set()
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, ['cancelled'])
            self.assert_process_stopped(child)

    def assert_process_stopped(self, pid):
        status = Path(f'/proc/{pid}/stat')
        self.wait_for(lambda: not status.exists() or status.read_text().split()[2] == 'Z')

    def test_timeout_reaps_child_even_when_shell_has_already_exited(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / 'pid'
            script = ('import pathlib, subprocess, sys; '
                      'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
                      'pathlib.Path(sys.argv[1]).write_text(str(child.pid))')
            with self.assertRaises(menu.BuildError):
                menu.SourceQuery(timeout=0.15)([sys.executable, '-c', script, str(pid_file)])
            self.assert_process_stopped(int(pid_file.read_text()))


class TuiTests(unittest.TestCase):
    def setUp(self):
        self.settings = menu.Settings(Path('/fixture/source'), Path('/fixture/build-menu'))
        self.validate = mock.Mock()

    @contextlib.contextmanager
    def terminal_environment(self, no_color=False, colors=256, branch='fixture-branch',
                             commit='123abcd', status='Modified', subject='fixture commit title',
                             source_version='Fixture.1.0.0'):
        environment = dict(os.environ, TERM='xterm-256color')
        environment.pop('NO_COLOR', None)
        if no_color:
            environment['NO_COLOR'] = ''
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, environment, clear=True))
            for name in ('curs_set', 'set_escdelay', 'start_color', 'use_default_colors', 'init_pair'):
                stack.enter_context(mock.patch.object(tui.curses, name))
            stack.enter_context(mock.patch.object(tui.curses, 'has_colors', return_value=True))
            stack.enter_context(mock.patch.object(tui.curses, 'color_pair', side_effect=lambda pair: pair << 8))
            stack.enter_context(mock.patch.object(tui.curses, 'COLORS', colors, create=True))
            stack.enter_context(mock.patch.object(menu.Settings, 'git_branch',
                                                 new_callable=mock.PropertyMock, return_value=branch))
            stack.enter_context(mock.patch.object(menu.Settings, 'git_commit_info',
                                                 new_callable=mock.PropertyMock, return_value=(commit, subject)))
            stack.enter_context(mock.patch.object(menu.Settings, 'source_status',
                                                 new_callable=mock.PropertyMock, return_value=status))
            stack.enter_context(mock.patch.object(menu.Settings, 'source_version',
                                                 new_callable=mock.PropertyMock, return_value=source_version))
            stack.enter_context(mock.patch.object(tui, 'MetadataCache', side_effect=StaticMetadata))
            yield

    def console(self, screen, **kwargs):
        # Keep historical/candidate layout fixtures explicit; test production
        # defaults separately, without overriding either selected style.
        kwargs.setdefault('banner_style', 'classic')
        kwargs.setdefault('i_style', 'current')
        console = tui.Console(screen, self.settings, self.validate, menu.IMAGES, **kwargs)
        self.addCleanup(console.close)
        return console

    def test_wordmark_palette_and_green_i_stem(self):
        lines = tui.wordmark_lines()
        self.assertEqual(len(lines), 5)
        self.assertEqual([sum(tui.cells(text) for text, _ in row) for row in lines], [52] * 5)
        for row, parts in enumerate(lines):
            self.assertEqual(parts[1][1], 'green' if row >= 2 else 'blue')
            self.assertTrue(all(tone == 'blue' for index, (_, tone) in enumerate(parts) if index != 1))
        self.assertEqual(tui.BRAND_RGB, {'blue': (11, 104, 148), 'green': (138, 223, 76)})
        with self.terminal_environment():
            console = self.console(TuiScreen())
            tui.curses.init_pair.assert_any_call(1, 24, -1)
            tui.curses.init_pair.assert_any_call(2, 113, -1)
            tui.curses.init_pair.assert_any_call(5, tui.curses.COLOR_BLACK, 113)
            self.assertIn('selection', console.palette)

    def test_wordmark_has_full_height_letters_shared_baseline_and_even_spacing(self):
        for letter, glyph in tui.WORDMARK.items():
            with self.subTest(letter=letter):
                self.assertEqual(len(glyph), 5)
                self.assertTrue(all(row.strip() for row in glyph),
                                'Every letter, including w, must occupy all five rows')
                self.assertEqual(len({tui.cells(row) for row in glyph}), 1)
                self.assertTrue(all(char.isascii() and char.isprintable()
                                    for row in glyph for char in row))
        for row, parts in enumerate(tui.wordmark_lines()):
            for index, (text, _) in enumerate(parts):
                glyph = tui.WORDMARK['wistron'[index]][row]
                self.assertEqual(text, glyph + (' ' if index < 6 else ''))

    def test_banner_is_centered_padded_and_clear_of_controls_at_supported_sizes(self):
        for height, width in ((32, 110), (24, 62), (20, 48)):
            with self.subTest(height=height, width=width), self.terminal_environment():
                screen = TuiScreen(height=height, width=width)
                console = self.console(screen)
                console.draw_main()
                rows = [''.join(row) for row in screen.grid]
                _, frame_width, left = console.dimensions()
                expanded = height >= 24 and width >= 62
                self.assertIn('SONiC BUILD CONSOLE', rows[1])
                config_top = next(index for index, row in enumerate(rows)
                                  if 'BUILD CONFIGURATION' in row)
                self.assertEqual(config_top, 11 if expanded else 5)
                self.assertIn('0. exit', rows[config_top + 8])
                self.assertIn('ARM64:', rows[config_top + 1])
                column = min(26, max(23, frame_width // 3))
                self.assertEqual(rows[config_top + 2][left + column:left + frame_width - 2].strip(), 'Amazon')
                self.assertIn('Yes:', rows[config_top + 3])
                self.assertLess(config_top + 9, height - 2)
                self.assertIn('Enter select', rows[height - 2])
                branch_row = next(index for index, row in enumerate(rows) if 'fixture-branch' in row)
                self.assertGreater(branch_row, config_top + 9)
                self.assertLess(branch_row, height - 2)
                metadata = '\n'.join(rows[config_top + 10:height - 2])
                self.assertIn('123abcd', metadata)
                self.assertIn('Modified', metadata)
                if width == 110:
                    self.assertIn('123abcd fixture commit title', metadata)
                if expanded:
                    start = left + (frame_width - 52) // 2
                    self.assertLessEqual(abs((start - left - 1)
                                             - (left + frame_width - 1 - start - 52)), 1)
                    self.assertFalse(rows[2][left + 1:left + frame_width - 1].strip())
                    self.assertFalse(rows[8][left + 1:left + frame_width - 1].strip())
                    for row, parts in enumerate(tui.wordmark_lines()):
                        self.assertEqual(rows[row + 3][start:start + 52],
                                         ''.join(text for text, _ in parts))
                        x = start
                        for text, tone in parts:
                            expected = console.attr(tone, bold=True)
                            self.assertEqual(screen.styles[row + 3][x:x + len(text)],
                                             [expected] * len(text))
                            x += len(text)
                else:
                    self.assertIn('wistron', rows[2])

    def test_monochrome_banner_preserves_wordmark_and_responsive_layout(self):
        for height, width in ((32, 110), (24, 62), (20, 48)):
            with self.subTest(height=height, width=width):
                color = TuiScreen(height=height, width=width)
                mono = TuiScreen(height=height, width=width)
                with self.terminal_environment():
                    self.console(color).draw_main()
                with self.terminal_environment(no_color=True):
                    self.console(mono).draw_main()
                self.assertEqual(color.grid, mono.grid)
                self.assertTrue(all(not attr & tui.curses.A_COLOR
                                    for row in mono.styles for attr in row))

    def test_three_banner_candidates_keep_logo_padding_controls_and_source_card(self):
        for height, width in ((32, 110), (24, 62), (20, 48)):
            for mono in (False, True):
                with self.subTest(height=height, width=width, mono=mono), self.terminal_environment(no_color=mono):
                    baseline = TuiScreen(height=height, width=width)
                    self.console(baseline).draw_main()
                    drawings = []
                    for style in ('tech', 'brand', 'retro'):
                        screen = TuiScreen(height=height, width=width)
                        console = self.console(screen, banner_style=style)
                        console.draw_main()
                        rows = [''.join(row) for row in screen.grid]
                        expanded = height >= 24 and width >= 62
                        footer = 9 if expanded else 3
                        self.assertIn('SONiC BUILD CONSOLE', rows[1])
                        self.assertEqual(screen.grid[footer + 1:], baseline.grid[footer + 1:])
                        self.assertEqual(screen.styles[footer + 1:], baseline.styles[footer + 1:])
                        if expanded:
                            _, frame_width, left = console.dimensions()
                            start = left + (frame_width - 52) // 2
                            for row, parts in enumerate(tui.wordmark_lines(), 3):
                                self.assertEqual(rows[row][start:start + 52], ''.join(text for text, _ in parts))
                                self.assertEqual(screen.styles[row][start:start + 52],
                                                 baseline.styles[row][start:start + 52])
                            for row in (2, 8):
                                self.assertFalse(rows[row][left + 1:left + frame_width - 1].strip())
                        else:
                            self.assertIn('wistron', rows[2])
                        if mono:
                            self.assertTrue(all(not attr & tui.curses.A_COLOR for row in screen.styles for attr in row))
                        drawings.append('\n'.join(rows[1:footer + 1]))
                    self.assertEqual(len(set(drawings)), 3)

    def test_banner_candidates_ascii_fallback_and_navigation_do_not_redraw_idle(self):
        for style in ('tech', 'brand', 'retro'):
            with self.subTest(style=style), self.terminal_environment():
                screen = TuiScreen([-1, -1, tui.curses.KEY_DOWN, ord('0'), 10])
                console = self.console(screen, banner_style=style)
                console.border = ('+', '+', '+', '+', '-', '|')
                self.assertEqual(console.loop()[0], 'exit')
                self.assertEqual(len(screen.frames), 3)
                self.assertTrue(all(char.isascii() for row in screen.grid[1:10] for char in row))
                self.validate.assert_not_called()

    def test_dense_banners_preserve_full_height_width_controls_and_monochrome(self):
        for glyph in tui.DENSE_WORDMARK.values():
            self.assertEqual(len(glyph), 5)
            self.assertTrue(all(row.strip() for row in glyph))
            self.assertEqual(len({tui.cells(row) for row in glyph}), 1)
        self.assertEqual([sum(tui.cells(text) for text, _ in row)
                          for row in tui.dense_wordmark_lines()], [52] * 5)
        for height, width in ((32, 110), (24, 62), (20, 48)):
            for mono in (False, True):
                with self.subTest(height=height, width=width, mono=mono), self.terminal_environment(no_color=mono):
                    baseline = TuiScreen(height=height, width=width)
                    self.console(baseline).draw_main()
                    for style in ('neon', 'signature', 'pulse'):
                        screen = TuiScreen(height=height, width=width)
                        console = self.console(screen, banner_style=style)
                        console.draw_main()
                        expanded = height >= 24 and width >= 62
                        footer = 9 if expanded else 3
                        self.assertEqual(screen.grid[footer + 1:], baseline.grid[footer + 1:])
                        self.assertEqual(screen.styles[footer + 1:], baseline.styles[footer + 1:])
                        rows = [''.join(row) for row in screen.grid]
                        self.assertIn('SONiC BUILD CONSOLE', rows[1])
                        if expanded:
                            _, frame_width, left = console.dimensions()
                            start = left + (frame_width - 52) // 2
                            for row, parts in enumerate(tui.dense_wordmark_lines(), 3):
                                self.assertEqual(rows[row][start:start + 52], ''.join(text for text, _ in parts))
                            for row in (2, 8):
                                self.assertFalse(rows[row][left + 1:left + frame_width - 1].strip())
                        else:
                            self.assertIn('wistron', rows[2])
                        if mono:
                            self.assertFalse(console.palette)
                            self.assertTrue(all(not attr & tui.curses.A_COLOR for row in screen.styles for attr in row))

    def test_dense_banners_have_ascii_and_basic_color_fallbacks(self):
        for style in ('neon', 'signature', 'pulse'):
            with self.subTest(style=style), self.terminal_environment(colors=8):
                screen = TuiScreen([-1, tui.curses.KEY_DOWN, ord('0'), 10])
                console = self.console(screen, banner_style=style)
                console.border = ('+', '+', '+', '+', '-', '|')
                self.assertEqual(console.loop()[0], 'exit')
                self.assertEqual(len(screen.frames), 3)
                self.assertTrue(all(char.isascii() for row in screen.grid[1:10] for char in row))
                self.validate.assert_not_called()

    def test_i_candidates_only_change_i_and_preserve_selected_power_stripe_layout(self):
        baseline = tui.dense_wordmark_lines()
        for i_style in ('thin', 'bold', 'bar'):
            with self.subTest(i_style=i_style):
                candidate = tui.dense_wordmark_lines(i_style=i_style)
                self.assertEqual([sum(tui.cells(text) for text, _ in row) for row in candidate], [52] * 5)
                for row in range(5):
                    self.assertEqual(candidate[row][:1] + candidate[row][2:], baseline[row][:1] + baseline[row][2:])
                    if row >= (1 if i_style == 'bar' else 2):
                        self.assertEqual(candidate[row][1][1], 'green')
                glyph = tui.DENSE_I_GLYPHS[i_style]
                self.assertEqual(glyph[-1], glyph[2])
                self.assertEqual([len(line) for line in glyph], [3] * 5)
                for height, width in ((32, 110), (24, 62), (20, 48)):
                    with self.terminal_environment():
                        original = TuiScreen(height=height, width=width)
                        self.console(original, banner_style='pulse').draw_main()
                        screen = TuiScreen(height=height, width=width)
                        self.console(screen, banner_style='pulse', i_style=i_style).draw_main()
                        footer = 9 if height >= 24 and width >= 62 else 3
                        self.assertEqual(screen.grid[footer + 1:], original.grid[footer + 1:])
                        self.assertEqual(screen.styles[footer + 1:], original.styles[footer + 1:])

    def test_production_default_is_power_stripe_with_bold_i(self):
        self.assertEqual(tui.DEFAULT_BANNER_STYLE, 'pulse')
        self.assertEqual(tui.DEFAULT_I_STYLE, 'bold')
        for mono in (False, True):
            for height, width in ((32, 110), (24, 62), (20, 48)):
                with self.subTest(mono=mono, height=height, width=width), self.terminal_environment(no_color=mono):
                    screen = TuiScreen(height=height, width=width)
                    console = tui.Console(screen, self.settings, self.validate, menu.IMAGES)
                    self.addCleanup(console.close)
                    console.draw_main()
                    self.assertEqual((console.banner_style, console.i_style), ('pulse', 'bold'))
                    rows = [''.join(row) for row in screen.grid]
                    if height >= 24 and width >= 62:
                        _, frame_width, left = console.dimensions()
                        start = left + (frame_width - 52) // 2
                        for row, parts in enumerate(tui.dense_wordmark_lines(i_style='bold'), 3):
                            self.assertEqual(rows[row][start:start + 52], ''.join(text for text, _ in parts))
                        self.assertIn('/// WISTRON NETWORKING ///', rows[9])
                        if not mono:
                            self.assertEqual(screen.styles[1][left], console.attr('lime_band'))
                            self.assertEqual(screen.styles[9][left], console.attr('lime_band'))
                    else:
                        self.assertIn('wistron', rows[2])
                    if mono:
                        self.assertFalse(console.palette)

    def test_no_color_keeps_monochrome_arrow_ui(self):
        screen = TuiScreen([tui.curses.KEY_DOWN, tui.curses.KEY_DOWN, ord('0'), 10])
        with self.terminal_environment(no_color=True):
            console = self.console(screen)
            self.assertEqual(console.palette, {})
            self.assertEqual(console.attr(selected=True), tui.curses.A_REVERSE)
            self.assertEqual(console.loop()[0], 'exit')
            tui.curses.start_color.assert_not_called()
        self.assertIn('> 3. clean rebuild', '\n'.join(screen.frames))

    def test_arrow_selection_changes_customer_and_escape_preserves_it(self):
        keys = [tui.curses.KEY_DOWN, 10, tui.curses.KEY_DOWN, 10,
                10, tui.curses.KEY_UP, 27, ord('0'), 10]
        screen = TuiScreen(keys)
        with self.terminal_environment():
            self.assertEqual(self.console(screen).loop()[0], 'exit')
        self.assertEqual(self.settings.flavor, 'wistron')
        self.assertRegex(screen.frames[-1], r'2\. customer +Wistron +[│|]')
        self.assertNotIn('ZTP OFF', screen.frames[-1])
        self.validate.assert_not_called()

    def test_clean_picker_supports_arrows_and_y_n_shortcuts(self):
        for keys, expected in [([tui.curses.KEY_DOWN, 10], 'normal'), ([ord('N'), 10], 'normal'),
                               ([ord('n'), ord('Y'), 10], 'clean'), ([tui.curses.KEY_DOWN, 27], 'clean')]:
            with self.subTest(keys=keys):
                self.settings.mode = 'clean'
                screen = TuiScreen([ord('3'), 10, *keys, ord('0'), 10])
                with self.terminal_environment():
                    self.console(screen).loop()
                self.assertEqual(self.settings.mode, expected)
                self.assertIn('CLEAN REBUILD', '\n'.join(screen.frames))
                self.assertIn('Yes: back up target and rebuild', '\n'.join(screen.frames))
                self.assertIn('No: retain outputs, caches disabled', '\n'.join(screen.frames))

    def test_job_editor_validates_and_supports_editing_and_default(self):
        screen = TuiScreen([ord('4'), 10, ord('0'), 10, 21, ord('1'), ord('2'),
                            tui.curses.KEY_LEFT, 127, ord('4'), tui.curses.KEY_END,
                            127, 10, ord('0'), 10])
        with self.terminal_environment():
            self.console(screen).loop()
        self.assertEqual(self.settings.jobs, 4)
        self.assertIn('Build job must be a positive integer.', '\n'.join(screen.frames))
        screen = TuiScreen([ord('4'), 10, 10, ord('0'), 10])
        with self.terminal_environment():
            self.console(screen).loop()
        self.assertEqual(self.settings.jobs, 8)

    def test_version_editor_validates_cancels_and_blank_restores_functions(self):
        version = 'Wistron_M.1.0.3'
        screen = TuiScreen([ord('5'), 10, ord('2'), 10, *map(ord, 'bad/version'), 10, 21,
                            *map(ord, version), 10, ord('5'), 10, 27, ord('0'), 10])
        with self.terminal_environment():
            self.console(screen).loop()
        self.assertEqual(self.settings.build_version, version)
        self.assertIn('Invalid version:', '\n'.join(screen.frames))
        self.assertIn('BUILD VERSION', '\n'.join(screen.frames))
        screen = TuiScreen([ord('5'), 10, ord('2'), 10, 10, ord('0'), 10])
        with self.terminal_environment():
            self.console(screen).loop()
        self.assertEqual(self.settings.build_version, '')
        self.assertIn('Default: Fixture.1.0.0', '\n'.join(screen.frames))

    def test_version_default_picker_cancel_and_live_source_value(self):
        self.settings.build_version = 'Custom.7'
        with self.terminal_environment():
            screen = TuiScreen([ord('5'), 10, 27, ord('0'), 10])
            self.console(screen).loop()
            self.assertEqual(self.settings.build_version, 'Custom.7')
            screen = TuiScreen([ord('5'), 10, ord('1'), 10, ord('0'), 10])
            self.console(screen).loop()
            self.assertEqual(self.settings.build_version, '')
            self.assertIn('1. Default: Fixture.1.0.0', '\n'.join(screen.frames))
            self.assertIn('2. Custom: enter a version', '\n'.join(screen.frames))
            self.assertIn('Default: Fixture.1.0.0', screen.frames[-1])
            cache = StaticMetadata(self.settings)
            def update(screen):
                cache.values['version'] = 'Pulled.3'
                cache.revision += 1
                return -1
            screen = TuiScreen([update, ord('d'), 10])
            console = tui.Console(screen, self.settings, self.validate, menu.IMAGES, metadata=cache)
            console.edit_version()
            self.assertIn('1. Default: Pulled.3', screen.frames[-1])
            self.assertEqual(self.settings.build_version, '')

    def test_source_and_build_paths_are_adjacent_or_hidden_together(self):
        for height, width in ((32, 110), (24, 62), (20, 48), (21, 48), (23, 48)):
            with self.subTest(height=height, width=width), self.terminal_environment():
                screen = TuiScreen(height=height, width=width)
                self.console(screen).draw_main()
                lines = [''.join(row) for row in screen.grid]
                source = [index for index, line in enumerate(lines) if 'Source:' in line]
                build = [index for index, line in enumerate(lines) if 'Build:' in line]
                self.assertEqual(bool(source), bool(build))
                if source:
                    self.assertEqual(build[0], source[0] + 1)
                if width == 110:
                    branch = next(index for index, line in enumerate(lines) if 'Git branch:' in line)
                    self.assertGreater(branch, build[0])

    def test_x86_is_visible_but_cannot_start_build(self):
        screen = TuiScreen([ord('1'), 10, tui.curses.KEY_DOWN, 10, ord('6'), 10, 10, ord('0'), 10])
        with self.terminal_environment():
            console = self.console(screen)
            self.assertEqual(console.loop()[0], 'exit')
            _, width, left = console.dimensions()
            column = min(26, max(23, width // 3))
            for row in (12, 17):
                self.assertEqual(screen.styles[row][left + 7], screen.styles[13][left + 7])
                self.assertEqual(screen.styles[row][left + column], screen.styles[13][left + column])
        self.assertEqual(self.settings.arch, 'amd64')
        self.validate.assert_not_called()
        self.assertIn('ARM64: Marvell Prestera', '\n'.join(screen.frames))
        self.assertIn('X86: AMD64 (not supported yet)', '\n'.join(screen.frames))
        self.assertIn('No build was started.', '\n'.join(screen.frames))

    def test_review_cancel_and_confirm_are_separate_actions(self):
        with self.terminal_environment():
            screen = TuiScreen([ord('6'), 10, 27, ord('0'), 10])
            self.assertEqual(self.console(screen).loop()[0], 'exit')
            screen = TuiScreen([ord('6'), 10, 10])
            self.assertEqual(self.console(screen).loop()[0], 'build')
        self.assertIn('START BUILD / REVIEW', '\n'.join(screen.frames))
        self.assertIn('Architecture: ARM64: Marvell Prestera', '\n'.join(screen.frames))
        self.assertIn('Git branch: fixture-branch', '\n'.join(screen.frames))
        self.assertIn('Git commit: 123abcd fixture commit title', '\n'.join(screen.frames))
        self.assertIn('Source status: Modified', '\n'.join(screen.frames))
        self.assertEqual(self.validate.call_count, 2)

    def test_navigation_and_typing_use_cache_and_idle_does_not_redraw(self):
        screen = TuiScreen([-1, -1, ord('x'), -1, tui.curses.KEY_DOWN,
                            ord('5'), 10, ord('2'), 10, -1, ord('V'), ord('.'), ord('2'),
                            -1, 10, ord('0'), 10])
        with self.terminal_environment():
            cache = StaticMetadata(self.settings)
            with mock.patch.object(menu.Settings, 'git_branch', new_callable=mock.PropertyMock) as branch, \
                 mock.patch.object(menu.Settings, 'source_version', new_callable=mock.PropertyMock) as version, \
                 mock.patch.object(self.settings, 'read_source_metadata') as query:
                console = tui.Console(screen, self.settings, self.validate, menu.IMAGES, metadata=cache)
                self.assertEqual(console.loop()[0], 'exit')
                branch.assert_not_called()
                version.assert_not_called()
                query.assert_not_called()
        self.assertEqual(self.settings.build_version, 'V.2')
        # One initial frame, two selection changes, picker, editor plus three characters,
        # return to the main view and exit selection. Timeouts/unknown keys add none.
        self.assertEqual(len(screen.frames), 11)

    def test_background_change_updates_popup_and_custom_version_wins(self):
        with self.terminal_environment():
            cache = StaticMetadata(self.settings)
            def refresh(screen):
                cache.values['branch'] = 'new-branch'
                cache.values['version'] = 'V.3'
                cache.revision += 1
                return -1
            screen = TuiScreen([refresh, 27])
            console = tui.Console(screen, self.settings, self.validate, menu.IMAGES, metadata=cache)
            console.choose('CUSTOMER', ['Amazon (ZTP)', 'Wistron (no ZTP)'], 0)
            self.assertIn('new-branch', screen.frames[-1])
            self.assertEqual(console.version_display, 'Default: V.3')
            self.settings.build_version = 'Custom.1'
            console.draw_main()
            self.assertIn('Custom.1', screen.frames[-1])
            self.assertNotIn('Default: V.3', screen.frames[-1])
            self.settings.build_version = ''
            console.draw_main()
            self.assertIn('Default: V.3', screen.frames[-1])

    def test_slow_background_reader_does_not_block_keys_and_closes_before_build(self):
        cache_type = tui.MetadataCache
        started = threading.Event()
        def slow(stop):
            started.set()
            stop.wait(1)
            return dict.fromkeys(tui.metadata_defaults(''))
        with self.terminal_environment():
            with mock.patch.object(self.settings, 'read_source_metadata', side_effect=slow):
                cache = cache_type(self.settings)
                self.addCleanup(cache.close)
                self.assertTrue(started.wait(1))
                screen = TuiScreen([tui.curses.KEY_DOWN, tui.curses.KEY_UP, ord('0'), 10])
                console = tui.Console(screen, self.settings, self.validate, menu.IMAGES, metadata=cache)
                start = time.monotonic()
                self.assertEqual(console.loop()[0], 'exit')
                self.assertLess(time.monotonic() - start, 0.5)
                self.assertIn('Loading...', screen.frames[0])
                console.close()
                self.assertFalse(cache.thread.is_alive())
        caches = []
        with self.terminal_environment():
            def factory(settings):
                cache = StaticMetadata(settings)
                caches.append(cache)
                return cache
            screens = [TuiScreen([ord('6'), 10, 10]), TuiScreen([10, ord('0'), 10])]
            def build():
                self.assertTrue(caches[-1].closed)
                return {'status': 'success'}
            with mock.patch.object(tui, 'MetadataCache', side_effect=factory), \
                 mock.patch.object(tui.curses, 'wrapper', side_effect=lambda callback: callback(screens.pop(0))):
                self.assertEqual(tui.run_menu(self.settings, build, self.validate, menu.IMAGES), 0)
            self.assertTrue(all(cache.closed for cache in caches))

    def test_review_uses_fresh_read_instead_of_display_cache(self):
        with self.terminal_environment():
            cache = StaticMetadata(self.settings)
            fresh = {'branch': 'review-branch', 'commit': ('456def', 'new title'),
                     'status': 'Clean', 'version': 'V.4'}
            cache.refresh_now = mock.Mock(return_value=fresh)
            screen = TuiScreen([ord('6'), 10, 10])
            console = tui.Console(screen, self.settings, self.validate, menu.IMAGES, metadata=cache)
            self.assertEqual(console.loop()[0], 'build')
            cache.refresh_now.assert_called_once()
            frame = screen.frames[-1]
            self.assertIn('Git branch: review-branch', frame)
            self.assertIn('Git commit: 456def new title', frame)
            self.assertIn('Source status: Clean', frame)
            self.assertIn('Build version: Default: V.4', frame)

    def test_linear_fit_preserves_unicode_ansi_and_combining_widths(self):
        def previous_fit(value, width):
            result = ''
            for char in tui.plain(value):
                if tui.cells(result + char) > max(0, width):
                    break
                result += char
            return result
        values = ('abc', '中A文', 'e\u0301x', '\u0301A', '\x1b[32m中\x1b[0m A\nB', 'ＡB')
        for value in values:
            for width in range(-1, 8):
                self.assertEqual(tui.fit(value, width), previous_fit(value, width))

    def test_preflight_validation_failure_stays_in_ui(self):
        self.validate.side_effect = menu.BuildError('source conflict fixture')
        screen = TuiScreen([ord('6'), 10, 10, ord('0'), 10])
        with self.terminal_environment():
            self.assertEqual(self.console(screen).loop()[0], 'exit')
        self.assertIn('source conflict fixture', '\n'.join(screen.frames))

    def test_resize_and_small_terminals_do_not_start_build(self):
        def resize(screen):
            screen.height, screen.width = 20, 48
            screen.erase()
            return tui.curses.KEY_RESIZE
        screen = TuiScreen([resize, ord('2'), 10, 27, ord('0'), 10])
        with self.terminal_environment():
            self.assertEqual(self.console(screen).loop()[0], 'exit')
        screen = TuiScreen([ord('0')], height=12, width=30)
        with self.terminal_environment():
            self.assertEqual(self.console(screen).loop()[0], 'exit')
        self.assertIn('Terminal too small', '\n'.join(screen.frames))
        self.validate.assert_not_called()

    def test_wrapped_result_view_scrolls_to_all_lines(self):
        screen = TuiScreen([tui.curses.KEY_NPAGE] * 6 + [10], height=24, width=64)
        with self.terminal_environment():
            self.console(screen).message('RESULT', ['line-' + str(index) for index in range(50)])
        frames = '\n'.join(screen.frames)
        self.assertIn('line-0', frames)
        self.assertIn('line-49', frames)
        self.assertEqual(tui.fit('\u4e2dA', 2), '\u4e2d')
        self.assertEqual(''.join(tui.wrap('/very/long/path' * 20, 20)), '/very/long/path' * 20)

    def test_terminal_is_restored_before_build_and_result_returns_to_ui(self):
        for outcome in ('success', 'failed', 'interrupted'):
            with self.subTest(outcome=outcome):
                events = []
                active = False
                screens = [TuiScreen([ord('6'), 10, 10]), TuiScreen([10, ord('0'), 10])]
                def wrapper(callback):
                    nonlocal active
                    active = True
                    events.append('ui-enter')
                    try:
                        return callback(screens.pop(0))
                    finally:
                        active = False
                        events.append('ui-exit')
                def build():
                    self.assertFalse(active)
                    events.append('build')
                    if outcome == 'failed':
                        raise menu.BuildError('build failed fixture')
                    if outcome == 'interrupted':
                        raise KeyboardInterrupt
                    return {'status': 'success', 'sha256': 'fixture'}
                with self.terminal_environment(), mock.patch.object(tui.curses, 'wrapper', side_effect=wrapper):
                    self.assertEqual(tui.run_menu(self.settings, build, self.validate, menu.IMAGES), 0)
                self.assertEqual(events, ['ui-enter', 'ui-exit', 'build', 'ui-enter', 'ui-exit'])

    def test_ctrl_c_cancels_selection_and_reopens_ui(self):
        screens = [TuiScreen([KeyboardInterrupt()]), TuiScreen([ord('0'), 10])]
        build = mock.Mock()
        with self.terminal_environment():
            with mock.patch.object(tui.curses, 'wrapper', side_effect=lambda callback: callback(screens.pop(0))):
                self.assertEqual(tui.run_menu(self.settings, build, self.validate, menu.IMAGES), 0)
        build.assert_not_called()

    def test_main_routes_tty_to_tui_and_preserves_plain_fallback(self):
        for term, stdin, stdout, expected in [('xterm-256color', TerminalBuffer(), TerminalBuffer(), 'tui'),
                                             ('dumb', TerminalBuffer(), TerminalBuffer(), 'plain'),
                                             ('xterm-256color', io.StringIO(), TerminalBuffer(), 'plain'),
                                             ('xterm-256color', TerminalBuffer(), io.StringIO(), 'plain')]:
            with self.subTest(term=term, expected=expected):
                with mock.patch.dict(os.environ, {'TERM': term}):
                    with mock.patch.object(sys, 'stdin', stdin), mock.patch.object(sys, 'stdout', stdout):
                        with mock.patch.object(tui, 'run_menu', return_value=0) as rich, mock.patch.object(menu, 'menu', return_value=0) as plain:
                            self.assertEqual(menu.interactive_menu(self.settings), 0)
                self.assertEqual(rich.called, expected == 'tui')
                self.assertEqual(plain.called, expected == 'plain')

    def test_import_or_curses_failure_restores_numeric_fallback(self):
        with mock.patch.dict(os.environ, {'TERM': 'xterm-256color'}):
            with mock.patch.object(sys, 'stdin', TerminalBuffer()), mock.patch.object(sys, 'stdout', TerminalBuffer()):
                with mock.patch.object(tui, 'run_menu', side_effect=tui.curses.error('fixture terminal error')):
                    with mock.patch.object(menu, 'menu', return_value=0) as fallback:
                        self.assertEqual(menu.interactive_menu(self.settings), 0)
                        fallback.assert_called_once()


if __name__ == '__main__':
    unittest.main()
