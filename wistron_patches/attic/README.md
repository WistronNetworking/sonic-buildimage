Patches retired from the active set (branch wistron/es1227-54ts-p2-trixie-m1.0.2):

Already merged into committed source (do not re-apply):
- 0001-1-based-port-mapping.patch, 0001-set-sfp-port-default-speed-to-10G.patch,
  0001-Add-dhcp_l2-dhcpv6_l2-to-copp_cfg.json.patch, 0013-trixie-mvsai-kernel-interface.patch,
  0014-trixie-build-fixes.patch

Obsolete for trixie/6.12 / M.1.0.2 (targeted the old 6.1 kernel layout or older release):
- 0001-sonic-kernel-modification-for-wistron.patch, 0007-version_for_release.patch

Split / superseded (do not re-apply whole file):
- 0005-es1227-54ts-board-device-tree-fixes.patch
  - RTC ds1388→ds1338 → active 0020-es1227-54ts-rtc-ds1338.patch
  - drop pca9555 @0x20 → active 0022-es1227-54ts-drop-pca9555.patch

Never part of the validated M.1.0.2 build (failed to apply in the original tree; re-evaluate
deliberately before resurrecting):
- 0012-sonic-port-yang-add-poe-fields.patch

Promoted to active wistron_patches/ (apply via apply_patches.sh):
- 0008-pmon-usb-mount-fix.patch
- 0020-es1227-54ts-rtc-ds1338.patch (from 0005 RTC half)
- 0022-es1227-54ts-drop-pca9555.patch (from 0005 pca9555 half)
