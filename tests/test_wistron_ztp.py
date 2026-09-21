"""ZTP patch and hook regressions; no network interfaces or services are changed.

Initialize src/sonic-ztp first, or set SONIC_ZTP_SOURCE to a checkout containing
the pinned submodule commit. Run: python3 -m unittest discover -s tests -p test_wistron_ztp.py
"""

import concurrent.futures
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("SONIC_ZTP_SOURCE", ROOT / "src/sonic-ztp"))
PROFILE = "src/sonic-ztp/src/usr/lib/ztp/ztp-profile.sh"
HOOK = "src/sonic-ztp/src/usr/lib/ztp/dhcp/ztp"


def run(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, **kwargs)


def executable(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)


class ZtpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_tmp = tempfile.TemporaryDirectory(prefix="wistron-ztp-fixture-")
        cls.addClassCleanup(cls.fixture_tmp.cleanup)
        cls.fixture = Path(cls.fixture_tmp.name)
        sha = run(["git", "rev-parse", "HEAD:src/sonic-ztp"], cwd=ROOT).stdout.strip()
        for name in (PROFILE, HOOK):
            source_path = name[len("src/sonic-ztp/"):]
            result = run(["git", "show", sha + ":" + source_path], cwd=SOURCE)
            if result.returncode:
                raise unittest.SkipTest("Initialize pinned src/sonic-ztp or set SONIC_ZTP_SOURCE")
            destination = cls.fixture / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(result.stdout)
        name = "files/image_config/interfaces/interfaces-config.sh"
        destination = cls.fixture / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(run(["git", "show", "HEAD:" + name], cwd=ROOT).stdout)
        shutil.copyfile(ROOT / "apply_patches.sh", cls.fixture / "apply_patches.sh")
        shutil.copytree(ROOT / "wistron_patches/ztp_workaround", cls.fixture / "wistron_patches/ztp_workaround")
        # CoPP is independent of the shell patch stack. Use a small fixture so
        # these tests do not require downloading or building sonic-swss.
        (cls.fixture / "copp.txt").write_text("old\n")
        (cls.fixture / "wistron_patches/ztp_workaround/0002-Add-dhcp_l2-dhcpv6_l2-to-copp-supported-list.patch").write_text(
            "diff --git a/copp.txt b/copp.txt\n--- a/copp.txt\n+++ b/copp.txt\n@@ -1 +1 @@\n-old\n+new\n")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wistron-ztp-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(self.fixture, self.root, dirs_exist_ok=True)
        self.apply("-z")
        bins = self.root / "bin"
        executable(bins / "ztp", '#!/bin/sh\necho "${ZTP_STATUS:-1:ACTIVE}"\n')
        executable(bins / "logger", "#!/bin/sh\nexit 0\n")
        executable(bins / "ip", '''#!/bin/sh
printf '%s\n' "$*" >> "$IP_LOG"
case "$*" in
    "route replace "*) case "$*" in *"dev ${FAIL_VLAN:-none} "*) exit 2;; esac;;
    "-4 -o addr show "*) [ -z "${LEASE_ADDRESS:-}" ] || echo "1: $interface inet $LEASE_ADDRESS/24";;
esac
exit 0
''')
        self.state = self.root / "state"
        self.env = dict(os.environ, PATH=str(bins) + ":" + os.environ["PATH"],
                        ZTP_RUN_DIR=str(self.state), IP_LOG=str(self.root / "ip.log"))

    def apply(self, action):
        result = run(["bash", "apply_patches.sh", action], cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def event(self, interface, reason, url="", **extra):
        env = dict(self.env, interface=interface, reason=reason,
                   new_bootfile_name=url, new_dhcp_server_identifier="192.0.2.10",
                   new_routers="192.0.2.1", **extra)
        result = run(["dash", str(self.root / HOOK)], env=env)
        self.assertEqual(result.returncode, 0, result.stderr)

    def read_state(self, name):
        path = self.state / name
        return path.read_text().strip() if path.exists() else None

    def assert_owner(self, owner, url):
        self.assertEqual(self.read_state("ztp.lock/interface"), owner)
        self.assertEqual(self.read_state("dhcp_67-ztp_data_url"), url)

    def test_apply_twice_preserves_files_and_unrelated_edits(self):
        profile = self.root / PROFILE
        profile.write_text(profile.read_text().replace("#!/bin/bash\n", "#!/bin/bash\n# unrelated local comment\n", 1))
        before = profile.read_text()
        self.apply("-z")
        self.assertEqual(profile.read_text(), before)
        self.assertFalse(list(self.root.rglob("*.rej")))

    def test_revert_and_reapply(self):
        self.apply("ZTP=no")
        for name in (PROFILE, HOOK, "files/image_config/interfaces/interfaces-config.sh"):
            self.assertEqual((self.root / name).read_bytes(), (self.fixture / name).read_bytes())
        self.apply("-z")
        self.apply("-z")

    def test_apply_after_only_older_ztp_patch(self):
        self.apply("ZTP=no")
        patch = self.root / "wistron_patches/ztp_workaround/0002-ztp-workaround-mac-table-added.patch"
        result = run(["patch", "-p1", "--batch", "--forward"], cwd=self.root, input=patch.read_text())
        self.assertEqual(result.returncode, 0, result.stdout)
        self.apply("-z")

    def test_normal_handoff_and_final_release(self):
        self.event("Ethernet11", "BOUND", "http://ethernet/ztp.json")
        self.event("Vlan951", "BOUND", "http://vlan/ztp.json")
        self.event("Ethernet11", "RELEASE")
        self.assert_owner("dhcp:Vlan951", "http://vlan/ztp.json")
        self.event("Vlan951", "RELEASE")
        self.assert_owner(None, None)
        self.assertIn("route del 192.0.2.10/32", (self.root / "ip.log").read_text())

    def test_option_withdrawal_promotes_backup(self):
        self.event("Vlan951", "BOUND", "http://old/ztp.json")
        self.event("Ethernet11", "BOUND", "http://backup/ztp.json")
        self.event("Vlan951", "RENEW")
        self.assert_owner("dhcp:Ethernet11", "http://backup/ztp.json")
        self.assertFalse((self.state / "dhcp-candidates/Vlan951").exists())

    def test_option_withdrawal_without_backup_clears_owner(self):
        self.event("Vlan951", "BOUND", "http://old/ztp.json")
        self.event("Vlan951", "RENEW")
        self.assert_owner(None, None)

    def test_route_failure_does_not_hide_other_candidates(self):
        self.event("Ethernet11", "BOUND", "http://backup/ztp.json")
        self.event("Vlan951", "BOUND", "http://owner/ztp.json")
        self.event("Vlan10", "BOUND", "http://failed/ztp.json", FAIL_VLAN="Vlan10")
        self.event("Vlan951", "RELEASE", FAIL_VLAN="Vlan10")
        self.assert_owner("dhcp:Ethernet11", "http://backup/ztp.json")

    def test_timeout_requires_configured_lease_address(self):
        self.event("Vlan951", "TIMEOUT", "http://vlan/ztp.json", new_ip_address="192.0.2.2", LEASE_ADDRESS="192.0.2.2")
        self.assert_owner("dhcp:Vlan951", "http://vlan/ztp.json")
        self.event("Vlan951", "TIMEOUT", "http://vlan/ztp.json", new_ip_address="192.0.2.2")
        self.assert_owner(None, None)

    def test_ipv6_owner_is_not_overridden_by_ipv4(self):
        self.event("eth0", "BOUND6", new_dhcp6_boot_file_url="http://ipv6/ztp.json")
        self.event("Vlan951", "BOUND", "http://vlan/ztp.json")
        self.assert_owner("dhcp6:eth0", None)
        self.event("eth0", "RELEASE6")
        self.assert_owner("dhcp:Vlan951", "http://vlan/ztp.json")

    def test_concurrent_bound_and_release(self):
        for _ in range(20):
            self.event("Ethernet11", "BOUND", "http://ethernet/ztp.json")
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.event, "Vlan951", "BOUND", "http://vlan/ztp.json"),
                           pool.submit(self.event, "Ethernet11", "RELEASE")]
                for future in futures:
                    future.result()
            self.assert_owner("dhcp:Vlan951", "http://vlan/ztp.json")
            self.event("Vlan951", "RELEASE")

    def stop_client(self, fail=False, reused=False):
        for name in ("markers", "pids", "leases"):
            (self.root / name).mkdir()
        (self.root / "markers/Vlan951").write_text("123 456\n")
        (self.root / "pids/dhclient.Vlan951.pid").write_text("123\n")
        (self.root / "live").touch()
        (self.root / "address").touch()
        (self.root / "route").touch()
        executable(self.root / "bin/dhclient", '''#!/bin/sh
[ ! -e /proc/$$/fd/8 ] || exit 98
printf '%s\n' "$*" > "$TEST_ROOT/stop.args"
[ "$STOP_FAIL" != yes ] || exit 1
rm -f "$TEST_ROOT/live" "$TEST_ROOT/address" "$TEST_ROOT/route"
touch "$TEST_ROOT/stop-hook-finished"
''')
        source = (self.root / PROFILE).read_text()
        functions = source[source.index("vlan_dhclient_process_matches() {"):source.index("hook_dhcp_for_vlans() {")]
        functions += '''
VLAN_DHCLIENT_MARKER_DIR="$TEST_ROOT/markers"
VLAN_DHCLIENT_PID_DIR="$TEST_ROOT/pids"
VLAN_DHCLIENT_LEASE_DIR="$TEST_ROOT/leases"
vlan_dhclient_pid() { cat "$TEST_ROOT/pids/dhclient.$1.pid"; }
vlan_dhclient_starttime() { echo "$STARTTIME"; }
vlan_dhclient_process_matches() { [ -f "$TEST_ROOT/live" ]; }
(stop_vlan_dhclients_locked) 8>"$TEST_ROOT/control.lock"
'''
        return run(["bash"], input=functions, env=dict(self.env, TEST_ROOT=str(self.root),
                    STOP_FAIL="yes" if fail else "no", STARTTIME="999" if reused else "456"))

    def test_owned_client_runs_stop_hooks_before_marker_removal(self):
        result = self.stop_client()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "stop-hook-finished").exists())
        self.assertFalse((self.root / "markers/Vlan951").exists())
        self.assertFalse((self.root / "address").exists())
        self.assertFalse((self.root / "route").exists())
        self.assertEqual((self.root / "stop.args").read_text().strip(),
                         "-x -pf {0}/pids/dhclient.Vlan951.pid -lf {0}/leases/dhclient.Vlan951.leases Vlan951".format(self.root))

    def test_failed_stop_retains_marker_and_pid_for_retry(self):
        result = self.stop_client(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / "markers/Vlan951").exists())
        self.assertTrue((self.root / "pids/dhclient.Vlan951.pid").exists())

    def test_reused_pid_is_neither_stopped_nor_unlinked(self):
        result = self.stop_client(reused=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "stop.args").exists())
        self.assertTrue((self.root / "pids/dhclient.Vlan951.pid").exists())


if __name__ == "__main__":
    unittest.main()
