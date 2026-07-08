########################################################################
#
# Module contains an implementation of SONiC Platform Base API and
# provides the Components' (e.g., BIOS, CPLD, FPGA, etc.) available in
# the platform
#
########################################################################

try:
    import sys
    import subprocess
    import re
    from sonic_platform_base.component_base import (
        ComponentBase,
        FW_AUTO_SCHEDULED,
        FW_AUTO_ERR_IMAGE,
        FW_AUTO_INSTALLED,
        FW_AUTO_ERR_BOOT_TYPE,
        FW_AUTO_ERR_UNKNOWN
    )
    from . import eeprom
    from sonic_platform_base.sonic_eeprom import eeprom_tlvinfo
except ImportError as e:
    raise ImportError(str(e) + "- required module not found")


if sys.version_info[0] < 3:
    import commands as cmd
else:
    import subprocess as cmd


class Component(ComponentBase):
    """platform-specific Component class"""

    CHASSIS_COMPONENTS = [
        ["U-Boot", "Performs initialization during booting"],
        ["ONIE-VERSION", "ONIE - Open Network Install Environment"],
        ["CPLD", "CPLD firmware"],
    ]

    def __init__(self, component_index):
        self.index = component_index
        self.name = self.CHASSIS_COMPONENTS[self.index][0]
        self.description = self.CHASSIS_COMPONENTS[self.index][1]

    def _get_command_result(self, cmdline):
        try:
            proc = subprocess.Popen(cmdline.split(), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
            stdout = proc.communicate()[0]
            proc.wait()
            result = stdout.rstrip('\n')
        except OSError:
            result = None

        return result


    def get_name(self):
        """
        Retrieves the name of the component

        Returns:
            A string containing the name of the component
        """
        return self.name

    def get_description(self):
        """
        Retrieves the description of the component

        Returns:
            A string containing the description of the component
        """
        return self.description

    def get_firmware_version(self):
        """
        Retrieves the firmware version of the component

        Returns:
            A string containing the firmware version of the component
        """

        if self.index == 0:
            command = "grep -a 'U-Boot ' /dev/mtd0 | tail -n 1 | awk -F 'U-Boot' '{print $2}' | awk '{print $1}'"
            cmdstatus, uboot_version = cmd.getstatusoutput(command)
            return uboot_version

        if self.index == 1:
            # ONIE update appends the new version string at the end of the EEPROM.
            # We parse the EEPROM binary directly to find the latest ONIE Version TLV,
            # using dynamically imported paths and constants to avoid hardcoding.
            try:
                # Fetch path and TLV code dynamically
                eeprom_path = eeprom.Tlv()._eeprom_path
                onie_tlv_code = eeprom_tlvinfo.TlvInfoDecoder._TLV_CODE_ONIE_VERSION
                
                with open(eeprom_path, 'rb') as f:
                    data = f.read()
                versions = []
                for i in range(len(data) - 2):
                    if data[i] == onie_tlv_code:
                        length = data[i+1]
                        if 0 < length < 64 and i + 2 + length <= len(data):
                            val = data[i+2 : i+2+length]
                            if all(32 <= b < 127 for b in val):
                                versions.append(val.decode('ascii'))
                if versions:
                    return versions[-1]
            except Exception:
                pass
                
            # Fallback to the original installation machine.conf
            cmdstatus, onie_version = cmd.getstatusoutput('grep ^onie_version /host/machine.conf | cut -f2 -d"="')
            return onie_version

        if self.index == 2:
            cmdstatus, cpld_ver = cmd.getstatusoutput('cat /sys/bus/i2c/devices/0-0033/cpld_rev')
            return cpld_ver

    def install_firmware(self, image_path):
        """
        Installs firmware to the component

        Args:
            image_path: A string, path to firmware image

        Returns:
            A boolean, True if install was successful, False if not
        """
        if self.index == 0:
            try:
                cmd_uboot = "flashcp -v {} /dev/mtd0".format(image_path)
                status, _ = cmd.getstatusoutput(cmd_uboot)
                if status != 0:
                    return False
                return True
            except Exception:
                return False
        elif self.index == 1:
            try:
                # 1. Copy file to staging area (ONIE Boot Partition)
                cmd_copy = "cp -f {} /host/onie-updater".format(image_path)
                status, _ = cmd.getstatusoutput(cmd_copy)
                if status != 0:
                    return False
                
                # 2. For Marvell platforms, the original onie_bootcmd is hardcoded to install mode.
                # We dynamically fetch it and replace whatever the reason is with 'update'
                status, output = cmd.getstatusoutput("fw_printenv -n onie_bootcmd")
                if status != 0:
                    return False
                onie_update_cmd = re.sub(r"onie_boot_reason\s+\w+", "onie_boot_reason update", output.strip())
                cmd_setenv_boot = "fw_setenv boot_once '{}'".format(onie_update_cmd)
                status, _ = cmd.getstatusoutput(cmd_setenv_boot)
                if status != 0:
                    return False

                return True
            except Exception:
                return False
        elif self.index == 2:
            try:
                cmd_cpld = "updateCPLD 0x1 0x40 {}".format(image_path)
                status, _ = cmd.getstatusoutput(cmd_cpld)
                if status != 0:
                    return False
                return True
            except Exception:
                return False

        return False

    def get_presence(self):
        """
        Retrieves the presence of the FAN
        Returns:
            bool: True if FAN is present, False if not
        """
        return True

    def get_model(self):
        """
        Retrieves the model number (or part number) of the device
        Returns:
            string: Model/part number of device
        """
        return 'N/A'

    def get_serial(self):
        """
        Retrieves the serial number of the device
        Returns:
            string: Serial number of device
        """
        return 'N/A'

    def get_status(self):
        """
        Retrieves the operational status of the device
        Returns:
            A boolean value, True if device is operating properly, False if not
        """
        return True

    def get_position_in_parent(self):
        """
        Retrieves 1-based relative physical position in parent device.
        If the agent cannot determine the parent-relative position
        for some reason, or if the associated value of
        entPhysicalContainedIn is'0', then the value '-1' is returned
        Returns:
            integer: The 1-based relative physical position in parent device
            or -1 if cannot determine the position
        """
        return -1

    def is_replaceable(self):
        """
        Indicate whether this device is replaceable.
        Returns:
            bool: True if it is replaceable.
        """
        return False

    def get_available_firmware_version(self, image_path):
        """
        Retrieves the available firmware version of the component
        Note: the firmware version will be read from image
        Args:
            image_path: A string, path to firmware image
        Returns:
            A string containing the available firmware version of the component
        """
        return "N/A"

    def get_firmware_update_notification(self, image_path):
        """
        Retrieves a notification on what should be done in order to complete
        the component firmware update
        Args:
            image_path: A string, path to firmware image
        Returns:
            A string containing the component firmware update notification if required.
            By default 'None' value will be used, which indicates that no actions are required
        """
        return "None"

    def update_firmware(self, image_path):
        """
        Updates firmware of the component
        This API performs firmware update: it assumes firmware installation and loading in a single call.
        In case platform component requires some extra steps (apart from calling Low Level Utility)
        to load the installed firmware (e.g, reboot, power cycle, etc.) - this will be done automatically by API
        Args:
            image_path: A string, path to firmware image
        Raises:
            RuntimeError: update failed
        """
        if self.index in [0, 1]:
            if self.install_firmware(image_path):
                cmd.getstatusoutput("reboot")
                return True
            return False
        # CPLD (index 2) requires hard power cycle, software reboot is insufficient.
        # Thus, it doesn't support the automated update_firmware flow.
        return False

    def auto_update_firmware(self, image_path, boot_type):
        """
        Updates firmware of the component automatically based on boot_type.
        """
        if self.index in [0, 1]:
            if boot_type not in ["cold", "none"]:
                return FW_AUTO_ERR_BOOT_TYPE
                
            if self.install_firmware(image_path):
                if boot_type == "none":
                    return FW_AUTO_INSTALLED
                return FW_AUTO_SCHEDULED
            else:
                return FW_AUTO_ERR_IMAGE
                
        elif self.index == 2:
            # CPLD requires hard power cycle, so "cold" reboot (software) is invalid.
            # We only support "none" (install only) for auto update, 
            # and rely on the external PDU to perform the actual power cycle.
            if boot_type != "none":
                return FW_AUTO_ERR_BOOT_TYPE
                
            if self.install_firmware(image_path):
                return FW_AUTO_INSTALLED
            else:
                return FW_AUTO_ERR_IMAGE
        
        return FW_AUTO_ERR_UNKNOWN

