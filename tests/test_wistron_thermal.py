"""Thermal shutdown regressions with mocked hardware; standard library only."""

import ast
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ROOT / "platform/marvell-prestera/sonic-platform-wistron"
SKU = PLATFORMS / "es1227_36ts_p"


def load_classes(path, names, **namespace):
    """Execute the actual class bodies without importing hardware bindings."""
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, ast.ClassDef) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


class ThermalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wistron-thermal-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log = mock.Mock()
        self.info_ns = load_classes(SKU / "sonic_platform/thermal_infos.py", ["ThermalInfo", "ChassisInfo"],
                                   math=math, ThermalPolicyInfoBase=object, thermal_json_object=lambda name: lambda cls: cls)
        thermal_ns = load_classes(SKU / "sonic_platform/thermal.py", ["Thermal"], ThermalBase=object)
        self.sensor = thermal_ns["Thermal"](0)
        self.sensor.get_presence = mock.Mock(return_value=True)
        self.sensor.get_temperature = mock.Mock(return_value=40.0)
        self.chassis = mock.Mock()
        self.chassis.get_num_thermals.return_value = 1
        self.chassis.get_thermal.return_value = self.sensor
        self.info = self.info_ns["ThermalInfo"]()

    def sample(self, temp):
        self.sensor.get_temperature.return_value = temp
        self.info.collect(self.chassis)

    def test_uses_existing_high_and_critical_thresholds(self):
        self.assertFalse(hasattr(self.sensor, "get_caution2_threshold"))
        self.sample(40)
        self.assertFalse(self.info.is_over_high_threshold())
        self.sample(90)
        self.assertTrue(self.info.is_over_high_threshold())
        self.assertFalse(self.info.is_over_high_critical_threshold())
        self.sample(95)
        self.assertTrue(self.info.is_over_high_critical_threshold())
        self.assertEqual(self.info.get_shutdown_temp_dict(), {"CPU Temp": 95.0})

    def test_persistent_high_temperature_and_recovery(self):
        self.sample(91)
        self.assertFalse(self.info.is_warm_up_and_over_high_threshold())
        self.sample(91)
        self.assertTrue(self.info.is_warm_up_and_over_high_threshold())
        self.sample(40)
        self.assertFalse(self.info.is_warm_up_and_over_high_threshold())
        self.assertEqual(self.info.get_shutdown_temp_dict(), {})
        self.sample(91)
        self.assertFalse(self.info.is_warm_up_and_over_high_threshold())

    def test_missing_or_invalid_sensor_does_not_retain_alarm(self):
        for value in (None, float("nan"), float("inf"), "N/A"):
            with self.subTest(value=value):
                self.sample(95)
                self.sample(value)
                self.assertFalse(self.info.is_over_high_critical_threshold())
                self.assertEqual(self.info.get_shutdown_temp_dict(), {})
        self.sample(95)
        self.sensor.get_presence.return_value = False
        self.info.collect(self.chassis)
        self.assertEqual(self.info.get_temp_dict(), {})

    def action(self):
        infos = types.ModuleType("review_platform.thermal_infos")
        infos.ThermalInfo = self.info_ns["ThermalInfo"]
        infos.ChassisInfo = self.info_ns["ChassisInfo"]
        constants = types.ModuleType("sonic_platform.chassis")
        constants.HOST_REBOOT_CAUSE_PATH = str(self.root / "host") + "/"
        constants.PMON_REBOOT_CAUSE_PATH = str(self.root / "pmon") + "/"
        constants.REBOOT_CAUSE_FILE = "reboot-cause.txt"
        patcher = mock.patch.dict("sys.modules", {"review_platform.thermal_infos": infos, "sonic_platform.chassis": constants})
        patcher.start()
        self.addCleanup(patcher.stop)
        namespace = load_classes(SKU / "sonic_platform/thermal_actions.py", ["SetFanSpeedAction", "SwitchPolicyAction"],
                                 __name__="review_platform.thermal_actions", __package__="review_platform", os=os,
                                 ThermalPolicyActionBase=object, thermal_json_object=lambda name: lambda cls: cls,
                                 sonic_logger=self.log, sonic_platform=mock.Mock())
        info = infos.ChassisInfo()
        info.collect(self.chassis)
        return namespace["SwitchPolicyAction"](), {"thermal_info": self.info, "chassis_info": info}

    def test_shutdown_records_cause_before_cpld_write_without_shell_commands(self):
        self.sample(95)
        action, data = self.action()
        order = []
        def power_down():
            self.assertEqual((self.root / "host/reboot-cause.txt").read_text(), "Thermal - CPU\n")
            self.assertEqual(order, ["sync"])
            return True
        self.chassis.power_down.side_effect = power_down
        with mock.patch("os.sync", side_effect=lambda: order.append("sync")), mock.patch("os.system") as shell:
            self.assertTrue(action.execute(data))
        shell.assert_not_called()

    def test_shutdown_failure_is_reported_and_retryable(self):
        self.sample(95)
        action, data = self.action()
        self.chassis.power_down.side_effect = [False, OSError("CPLD not ready"), True]
        with mock.patch("os.sync"), mock.patch("os.system") as shell:
            self.assertFalse(action.execute(data))
            self.assertFalse(action.execute(data))
            self.assertTrue(action.execute(data))
        shell.assert_not_called()
        self.log.log_error.assert_called()

    def test_normal_temperature_never_requests_shutdown(self):
        self.sample(40)
        action, data = self.action()
        with mock.patch("os.sync") as sync:
            action.execute(data)
        self.chassis.power_down.assert_not_called()
        sync.assert_not_called()

    def test_cause_write_failure_does_not_block_hardware_shutdown(self):
        self.sample(95)
        action, data = self.action()
        self.chassis.power_down.return_value = True
        with mock.patch("os.makedirs", side_effect=PermissionError("read-only")), mock.patch("os.sync"):
            self.assertTrue(action.execute(data))
        self.log.log_error.assert_called()

    def test_chassis_reports_sysfs_write_failure(self):
        namespace = load_classes(SKU / "sonic_platform/chassis.py", ["Chassis"],
                                 ChassisBase=object, CPLD_SYSFS_DIR=str(self.root), sonic_logger=self.log)
        chassis = namespace["Chassis"].__new__(namespace["Chassis"])
        self.assertTrue(chassis.power_down())
        self.assertEqual((self.root / "system_power_down").read_text(), "0")
        with mock.patch("builtins.open", side_effect=OSError("I2C write failed")):
            self.assertFalse(chassis.power_down())

    @unittest.skipUnless(shutil.which("cc"), "C compiler unavailable")
    def test_driver_returns_i2c_errors_to_sysfs_caller(self):
        source = (SKU / "modules/wistron_cpld.c").read_text()
        start = source.index("static ssize_t power_down_write(")
        function = source[start:source.index("\n}\n", start) + 3]
        harness = '''
#include <assert.h>
#include <errno.h>
#include <stdlib.h>
#include <sys/types.h>
struct device { int unused; };
struct device_attribute { int unused; };
struct i2c_client { int unused; };
static int list_lock, locked, read_result, write_result;
#define CPLD_SYS_POWER_DOWN_REG 1
static struct i2c_client *to_i2c_client(struct device *dev) { return (void *)dev; }
static int kstrtoint(const char *s, int base, int *value) { *value = atoi(s); return 0; }
static void mutex_lock(int *lock) { locked++; }
static void mutex_unlock(int *lock) { locked--; }
static int i2c_smbus_read_byte_data(struct i2c_client *c, int reg) { return read_result; }
static int i2c_smbus_write_byte_data(struct i2c_client *c, int reg, int value) { return write_result; }
'''
        harness += function + '''
int main(void) {
    struct device dev = {0};
    write_result = -EIO;
    assert(power_down_write(&dev, 0, "0", 1) == -EIO);
    assert(locked == 0);
    read_result = -ENXIO;
    assert(power_down_write(&dev, 0, "0", 1) == -ENXIO);
    assert(locked == 0);
    read_result = 1; write_result = 0;
    assert(power_down_write(&dev, 0, "0", 1) == 1);
    assert(locked == 0);
    assert(power_down_write(&dev, 0, "2", 1) == -EINVAL);
    return 0;
}
'''
        path = self.root / "power_down.c"
        path.write_text(harness)
        subprocess.run(["cc", "-o", str(self.root / "power_down"), str(path)], check=True)
        subprocess.run([str(self.root / "power_down")], check=True)



if __name__ == "__main__":
    unittest.main()
