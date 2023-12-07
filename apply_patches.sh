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
