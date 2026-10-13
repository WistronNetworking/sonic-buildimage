# Marvell SAI

BRANCH = master
ifeq ($(CONFIGURED_ARCH),arm64)
MRVL_SAI_VERSION = 1.17.1-2
else
# Match the official SONiC 202511 SAI for the other architectures.
MRVL_SAI_VERSION = 1.17.1-13
endif

MRVL_SAI = mrvllibsai_$(MRVL_SAI_VERSION)_$(PLATFORM_ARCH).deb
ifeq ($(CONFIGURED_ARCH),arm64)
# Wistron arm64 (trixie/6.12) uses the tracked 1.17.1-2 (P7.0.1) package:
# ARP VLAN-based -> port-based trapping fix (SAIPRST-5514), plus DHCP L2
# IPCL trap. Validated after a cold boot; matches EZB 1.14 XML.
$(MRVL_SAI)_PATH = $(PLATFORM_PATH)
SONIC_COPY_DEBS += $(MRVL_SAI)
else
MRVL_SAI_URL_PREFIX = https://github.com/Marvell-switching/sonic-marvell-binaries/raw/master/$(CONFIGURED_ARCH)/sai-plugin/$(BRANCH)
$(MRVL_SAI)_URL = $(MRVL_SAI_URL_PREFIX)/$(MRVL_SAI)
SONIC_ONLINE_DEBS += $(MRVL_SAI)
endif

$(MRVL_SAI)_SKIP_VERSION=y
$(eval $(call add_conflict_package,$(MRVL_SAI),$(LIBSAIVS_DEV)))
