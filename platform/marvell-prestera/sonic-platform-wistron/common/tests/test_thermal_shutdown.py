"""Exercise thermal halt requests and shutdown dispatch without hardware."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
COMMON = ROOT / "common"


class ThermalRequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.boot_id = self.directory / "boot_id"
        self.boot_id.write_text("current-boot\n")

    def load_module(self, sku):
        path = ROOT / sku / "sonic_platform" / "thermal_shutdown.py"
        logger = mock.Mock()
        common = types.ModuleType("sonic_py_common")
        common.logger = types.SimpleNamespace(Logger=lambda *_: logger)
        spec = importlib.util.spec_from_file_location("thermal_shutdown_" + sku, path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {"sonic_py_common": common}):
            spec.loader.exec_module(module)
        module.REQUEST_DIR = str(self.directory)
        module.REQUEST_PATH = str(self.directory / "request")
        module.BOOT_ID_PATH = str(self.boot_id)
        return module, logger

    def test_request_and_failures_on_both_platforms(self):
        for sku in ("es1227_36ts_p", "es1227_54ts_p2"):
            with self.subTest(sku=sku):
                module, logger = self.load_module(sku)
                request = Path(module.REQUEST_PATH)
                with mock.patch.object(module.os, "sync"), \
                        mock.patch.object(module.os.path, "exists", return_value=False):
                    self.assertTrue(module.request_thermal_halt())
                self.assertEqual(request.read_text(), "current-boot\n")
                request.unlink()

                module.REQUEST_TIMEOUT = 0
                with mock.patch.object(module.os, "sync"):
                    self.assertFalse(module.request_thermal_halt())
                logger.log_error.assert_any_call("thermal shutdown: host did not accept halt request")
                request.unlink()

                with mock.patch.object(module.os, "sync", side_effect=OSError("sync failed")), \
                        mock.patch.object(module.os, "replace", side_effect=OSError("replace failed")):
                    self.assertFalse(module.request_thermal_halt())
                self.assertFalse(request.exists())
                self.assertFalse(list(self.directory.glob(".wistron-thermal-halt-*")))
                self.assertTrue(any("sync failed" in str(call) for call in logger.log_error.call_args_list))
                self.assertTrue(any("replace failed" in str(call) for call in logger.log_error.call_args_list))


class ThermalActionTests(unittest.TestCase):
    def test_both_platform_actions_record_cause_and_request_halt(self):
        for sku in ("es1227_36ts_p", "es1227_54ts_p2"):
            with self.subTest(sku=sku), tempfile.TemporaryDirectory() as directory:
                host = Path(directory) / "host"
                pmon = Path(directory) / "pmon"
                host.mkdir()
                pmon.mkdir()
                package = types.ModuleType("sonic_platform")
                package.__path__ = [str(ROOT / sku / "sonic_platform")]
                platform = types.ModuleType("sonic_platform.platform")
                package.platform = platform
                thermal = types.ModuleType("sonic_platform.thermal")
                thermal.Thermal = type("Thermal", (), {})
                chassis = types.ModuleType("sonic_platform.chassis")
                chassis.HOST_REBOOT_CAUSE_PATH = str(host) + "/"
                chassis.PMON_REBOOT_CAUSE_PATH = str(pmon) + "/"
                chassis.REBOOT_CAUSE_FILE = "reboot-cause.txt"
                infos = types.ModuleType("sonic_platform.thermal_infos")

                class ThermalInfo:
                    INFO_NAME = "thermal_info"

                    def get_temp_dict(self):
                        return {"CPU Temp": 100.0}

                    get_shutdown_temp_dict = get_temp_dict

                infos.ThermalInfo = ThermalInfo
                shutdown = types.ModuleType("sonic_platform.thermal_shutdown")
                shutdown.request_thermal_halt = mock.Mock(return_value=True)
                base = types.ModuleType("sonic_platform_base")
                base.__path__ = []
                control = types.ModuleType("sonic_platform_base.sonic_thermal_control")
                control.__path__ = []
                action_base = types.ModuleType("sonic_platform_base.sonic_thermal_control.thermal_action_base")
                action_base.ThermalPolicyActionBase = object
                json_object = types.ModuleType("sonic_platform_base.sonic_thermal_control.thermal_json_object")
                json_object.thermal_json_object = lambda _name: lambda cls: cls
                common = types.ModuleType("sonic_py_common")
                common.daemon_base = mock.Mock()
                common.logger = types.SimpleNamespace(Logger=lambda *_: mock.Mock())
                modules = {
                    "sonic_platform": package,
                    "sonic_platform.platform": platform,
                    "sonic_platform.thermal": thermal,
                    "sonic_platform.chassis": chassis,
                    "sonic_platform.thermal_infos": infos,
                    "sonic_platform.thermal_shutdown": shutdown,
                    "sonic_platform_base": base,
                    "sonic_platform_base.sonic_thermal_control": control,
                    "sonic_platform_base.sonic_thermal_control.thermal_action_base": action_base,
                    "sonic_platform_base.sonic_thermal_control.thermal_json_object": json_object,
                    "sonic_py_common": common,
                }
                path = ROOT / sku / "sonic_platform" / "thermal_actions.py"
                spec = importlib.util.spec_from_file_location("sonic_platform.thermal_actions", path)
                module = importlib.util.module_from_spec(spec)
                with mock.patch.dict(sys.modules, modules), mock.patch("os.sync"), mock.patch("os.fsync"):
                    spec.loader.exec_module(module)
                    module.SwitchPolicyAction().execute({"thermal_info": ThermalInfo()})
                shutdown.request_thermal_halt.assert_called_once_with()
                for target in (host, pmon):
                    self.assertEqual((target / "reboot-cause.txt").read_text(), "Thermal - CPU\n")


class HostDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.calls = self.directory / "calls"
        self.request = self.directory / "request"
        self.boot_id = self.directory / "boot_id"
        self.boot_id.write_text("current-boot\n")
        self.env = dict(os.environ, PATH=str(self.directory) + ":" + os.environ["PATH"],
                        CALLS=str(self.calls))
        self.stub("systemctl", 'echo "systemctl $*" >> "$CALLS"\n'
                  'case "$1" in stop) exit "${STOP_FAIL:-0}" ;; '
                  '--no-block) exit "${HALT_FAIL:-0}" ;; esac\n')
        self.stub("logger", 'echo "logger $*" >> "$CALLS"\n')
        self.stub("cfggen", 'echo "${PLATFORM:-arm64-wistron_es1227_54ts_p2-r0}"\n')
        self.stub("watchdogutil", 'echo "watchdogutil $*" >> "$CALLS"\n'
                  'exit "${DISARM_FAIL:-0}"\n')
        host = (COMMON / "wistron-thermal-halt").read_text()
        host = host.replace("/host/reboot-cause/.wistron-thermal-halt", str(self.request))
        host = host.replace("/proc/sys/kernel/random/boot_id", str(self.boot_id))
        host = host.replace("/usr/local/bin/sonic-cfggen", str(self.directory / "cfggen"))
        host = host.replace("/usr/local/bin/watchdogutil", str(self.directory / "watchdogutil"))
        self.host = self.directory / "host"
        self.host.write_text(host)
        self.host.chmod(0o755)

    def stub(self, name, body):
        path = self.directory / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def run_host(self, boot_id, **env):
        self.request.write_text(boot_id + "\n")
        return subprocess.run([str(self.host)], env=dict(self.env, **env),
                              capture_output=True, text=True)

    def test_current_request_stops_watchdog_and_halts(self):
        result = self.run_host("current-boot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.request.exists())
        calls = self.calls.read_text()
        self.assertIn("systemctl stop es1227_54ts_p2-watchdog.service", calls)
        self.assertIn("watchdogutil disarm", calls)
        self.assertIn("systemctl --no-block halt", calls)

    def test_stale_request_does_not_halt(self):
        result = self.run_host("previous-boot")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale", result.stderr)
        self.assertFalse(self.request.exists())
        self.assertNotIn("systemctl --no-block halt", self.calls.read_text())

    def test_36ts_request_stops_its_watchdog(self):
        result = self.run_host("current-boot", PLATFORM="arm64-wistron_es1227_36ts_p-r0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("systemctl stop es1227_36ts_p-watchdog.service", self.calls.read_text())

    def test_watchdog_failure_is_logged_but_halt_is_requested(self):
        result = self.run_host("current-boot", STOP_FAIL="1", DISARM_FAIL="1")
        self.assertEqual(result.returncode, 0)
        self.assertIn("could not stop", result.stderr)
        self.assertIn("could not disarm", result.stderr)
        self.assertIn("systemctl --no-block halt", self.calls.read_text())

    def test_halt_failure_is_logged(self):
        result = self.run_host("current-boot", HALT_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("host halt request failed", result.stderr)

    def test_shutdown_hook_only_writes_for_poweroff(self):
        target = self.directory / "system_power_down"
        target.write_text("1\n")
        hook = (COMMON / "wistron-cpld-poweroff").read_text()
        hook = hook.replace("/sys/bus/i2c/devices/0-0033/system_power_down", str(target))
        hook = hook.replace("/dev/kmsg", str(self.directory / "kmsg"))
        hook_path = self.directory / "hook"
        hook_path.write_text(hook)
        hook_path.chmod(0o755)
        for action in ("halt", "reboot", "kexec"):
            subprocess.run([str(hook_path), action], check=True)
            self.assertEqual(target.read_text(), "1\n")
        subprocess.run([str(hook_path), "poweroff"], check=True)
        self.assertEqual(target.read_text(), "0\n")
        target.unlink()
        target.mkdir()
        result = subprocess.run([str(hook_path), "poweroff"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed to write", result.stderr)


if __name__ == "__main__":
    unittest.main()
