# Marvell SAI

BRANCH = master
ifeq ($(CONFIGURED_ARCH),arm64)
MRVL_SAI_VERSION = 1.17.1-2
else ifeq ($(CONFIGURED_ARCH),armhf)
MRVL_SAI_VERSION = 1.17.1-1
else
MRVL_SAI_VERSION = 1.17.1-1
endif

# Use the locally-provided SAI deb shipped in $(PLATFORM_PATH) instead of the
# upstream online binary. arm64 (es1227 trixie/6.12) uses 1.17.1-2 (P7.0.1):
# adds the ARP VLAN-based -> port-based trapping fix (SAIPRST-5514) so routed/
# no-VLAN front ports trap ARP to CPU, plus DHCP L2 IPCL trap. Validated on DUT
# (routed-port ping 5/5, ARP REACHABLE) after a COLD boot; matches EZB 1.14 XML.
MRVL_SAI = mrvllibsai_$(MRVL_SAI_VERSION)_$(PLATFORM_ARCH).deb
$(MRVL_SAI)_PATH = $(PLATFORM_PATH)

SONIC_COPY_DEBS += $(MRVL_SAI)
$(MRVL_SAI)_SKIP_VERSION=y
$(eval $(call add_conflict_package,$(MRVL_SAI),$(LIBSAIVS_DEV)))

