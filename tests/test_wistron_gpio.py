"""SFP GPIO lookup regressions with mocked sysfs; standard library only."""

import ast
import io
import os
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ROOT / "platform/marvell-prestera/sonic-platform-wistron"


def load_classes(path, names, **namespace):
    """Execute the actual class bodies without importing hardware bindings."""
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, ast.ClassDef) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


class GpioTests(unittest.TestCase):
    def classes(self):
        for sku in ("es1227_54ts", "es2227_54ts", "es2227_54ts_p"):
            namespace = load_classes(PLATFORMS / sku / "sonic_platform/sfp.py", ["Sfp"],
                                     os=os, SfpOptoeBase=object, sonic_logger=mock.Mock())
            yield sku, namespace["Sfp"]

    @staticmethod
    def gpio_file(path, *args, **kwargs):
        return io.StringIO("5-0022\n" if str(path).endswith("/label") else "512\n")

    def test_initial_missing_chip_can_recover(self):
        for sku, sfp in self.classes():
            with self.subTest(sku=sku), mock.patch("os.listdir", side_effect=[[], ["gpiochip512"]]) as scan, \
                    mock.patch("builtins.open", side_effect=self.gpio_file):
                self.assertIsNone(sfp._gpio_base())
                self.assertEqual(sfp._gpio_base(), 512)
                self.assertEqual(sfp._gpio_base(), 512)
                self.assertEqual(scan.call_count, 2)

    def test_read_error_can_recover(self):
        for sku, sfp in self.classes():
            with self.subTest(sku=sku), mock.patch("os.listdir", return_value=["gpiochip512"]):
                with mock.patch("builtins.open", side_effect=OSError("temporarily unavailable")):
                    self.assertIsNone(sfp._gpio_base())
                with mock.patch("builtins.open", side_effect=self.gpio_file):
                    self.assertEqual(sfp._gpio_base(), 512)

    def test_existing_port_offsets_are_preserved(self):
        for sku, sfp in self.classes():
            with self.subTest(sku=sku):
                sfp._gpio_base_cache = 512
                instance = sfp.__new__(sfp)
                for port, offset in {50: 0, 49: 4, 52: 8, 51: 12, 54: 16, 53: 20}.items():
                    self.assertEqual(instance._gpio_num(sfp.PORT_PRESENT_GPIO_MAPPING[port]), 512 + offset)
                    self.assertEqual(instance._gpio_num(sfp.PORT_TX_DISABLE_GPIO_MAPPING[port]), 513 + offset)



if __name__ == "__main__":
    unittest.main()
