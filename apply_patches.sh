#!/bin/bash

#sonic-linux-kernel
patch -p1 < patches/0001-arm64-Enable-CONFIG_KEXEC_FILE.patch
patch -p1 < patches/0002-arm64-Select-CONFIG_PHY_MVEBU_CP110_COMPHY.patch
patch -p1 < patches/0003-arm64-Select-CONFIG_SPI_ORION.patch
patch -p1 < patches/0004-marvell-ac5-Support-boards-with-more-that-4G-DDR.patch

#sonic-sairedis
patch -p1 < patches/0001-SAI-switch-create-timeout-WA.patch

#sonic-utilities
patch -p1 < patches/0001-Marvell-generate_dump-utility.patch
patch -p1 < patches/0002-Use-kexec_load-syscall-for-stability.patch

#wistron
patch -p1 < patches/wistron_dts.patch
patch -p1 < patches/sonic-platform-common-sff-8472.patch
patch -p1 < patches/sfputil_sort_order.patch
patch -p1 < patches/show_ip_int_workaround.patch
patch -p1 < patches/rg_poe_test.patch
patch -p1 < patches/rg_poe_sonic_platform_base.patch
patch -p1 < patches/rg-poe-init-service.patch
patch -p1 < patches/rg_lldp_poe_alloc_power.patch
patch -p1 < patches/wistron_add_xmc_spi.patch
