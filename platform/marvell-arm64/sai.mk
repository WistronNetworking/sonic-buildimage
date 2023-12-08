# Marvell SAI

export MRVL_SAI_VERSION = 1.11.0-9
export MRVL_SAI = mrvllibsai_$(MRVL_SAI_VERSION)_$(PLATFORM_ARCH).deb

MRVL_SAI_DEB_LOCAL_URL = $(SRC_PATH)/mrvl_sai/
export MRVL_SAI_DEB_LOCAL_URL
#
ifneq ($(MRVL_SAI_DEB_LOCAL_URL), )
SAI_FROM_LOCAL = y
else
SAI_FROM_LOCAL = n
endif

ifeq ($(SAI_FROM_LOCAL), y)
$(MRVL_SAI)_PATH = $(MRVL_SAI_DEB_LOCAL_URL)/$(MRVL_SAI_VERSION)
SONIC_COPY_DEBS += $(MRVL_SAI)
$(eval $(call add_conflict_package,$(MRVL_SAI),$(LIBSAIVS_DEV)))
else
$(MRVL_SAI)_SRC_PATH = $(PLATFORM_PATH)/sai
$(eval $(call add_conflict_package,$(MRVL_SAI),$(LIBSAIVS_DEV)))
SONIC_MAKE_DEBS += $(MRVL_SAI)
endif
