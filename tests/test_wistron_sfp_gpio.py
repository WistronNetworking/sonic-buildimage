"""SFP GPIO discovery regressions using mocked sysfs, without hardware access."""

import ast
import io
import os
from pathlib import Path
import unittest
from unittest import mock


PLATFORMS = (Path(__file__).resolve().parents[1] /
             "platform/marvell-prestera/sonic-platform-wistron")
SKUS = ("es1227_54ts", "es2227_54ts", "es2227_54ts_p")


def load_sfp(sku):
    """Execute the real Sfp class with hardware-dependent imports stubbed."""
    path = PLATFORMS / sku / "sonic_platform/sfp.py"
    classes = [node for node in ast.parse(path.read_text()).body
               if isinstance(node, ast.ClassDef) and node.name == "Sfp"]
    namespace = dict(os=os, SfpOptoeBase=object, sonic_logger=mock.Mock())
    exec(compile(ast.Module(body=classes, type_ignores=[]), str(path), "exec"),
         namespace)
    return namespace["Sfp"]


def gpio_file(path, *args, **kwargs):
    return io.StringIO("5-0022\n" if str(path).endswith("/label") else "512\n")


class SfpGpioTests(unittest.TestCase):
    def test_missing_chip_recovers_and_success_is_cached(self):
        for sku in SKUS:
            with self.subTest(sku=sku):
                sfp = load_sfp(sku)
                instance = sfp.__new__(sfp)
                with mock.patch("os.listdir", side_effect=[[], ["gpiochip512"]]) as scan, \
                        mock.patch("builtins.open", side_effect=gpio_file):
                    self.assertIsNone(instance._gpio_num(0))
                    self.assertEqual(instance._gpio_num(0), 512)
                    self.assertEqual(instance._gpio_num(1), 513)
                    self.assertEqual(scan.call_count, 2)

    def test_transient_sysfs_errors_can_recover(self):
        for sku in SKUS:
            for failure in ("directory", "label", "base", "invalid_base"):
                with self.subTest(sku=sku, failure=failure):
                    sfp = load_sfp(sku)

                    def failing_read(path, *args, **kwargs):
                        if str(path).endswith("/" + failure):
                            raise OSError("temporarily unavailable")
                        if failure == "invalid_base" and str(path).endswith("/base"):
                            return io.StringIO("invalid\n")
                        return gpio_file(path)

                    with mock.patch("os.listdir", return_value=["gpiochip512"],
                                    side_effect=OSError("unavailable") if failure == "directory" else None), \
                            mock.patch("builtins.open", side_effect=failing_read):
                        sfp._gpio_base()
                    with mock.patch("os.listdir", return_value=["gpiochip512"]), \
                            mock.patch("builtins.open", side_effect=gpio_file):
                        self.assertEqual(sfp._gpio_base(), 512)

    def test_port_offsets_are_preserved(self):
        for sku in SKUS:
            with self.subTest(sku=sku):
                sfp = load_sfp(sku)
                instance = sfp.__new__(sfp)
                with mock.patch("os.listdir", return_value=["gpiochip512"]), \
                        mock.patch("builtins.open", side_effect=gpio_file):
                    for port, offset in {50: 0, 49: 4, 52: 8, 51: 12, 54: 16, 53: 20}.items():
                        for mapping, signal_offset in (
                                (sfp.PORT_PRESENT_GPIO_MAPPING, 0),
                                (sfp.PORT_TX_DISABLE_GPIO_MAPPING, 1),
                                (sfp.PORT_RX_LOS_GPIO_MAPPING, 2),
                                (sfp.PORT_TX_FAULT_GPIO_MAPPING, 3)):
                            self.assertEqual(instance._gpio_num(mapping[port]),
                                             512 + offset + signal_offset)


if __name__ == "__main__":
    unittest.main()
