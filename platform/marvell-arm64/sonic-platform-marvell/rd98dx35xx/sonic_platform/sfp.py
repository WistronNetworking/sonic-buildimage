import os
import sys

try:
    from sonic_platform_base.sfp_base import SfpBase
    from sonic_platform_base.sonic_sfp.sff8472 import sff8472InterfaceId
    from sonic_platform_base.sonic_sfp.sff8472 import sff8472Dom
    from sonic_platform_base.sonic_sfp.sfputilhelper import SfpUtilHelper
    from sonic_py_common import logger
except ImportError as e:
    raise ImportError(str(e) + "- required module not found")

if sys.version_info[0] < 3:
    import commands as cmd
else:
    import subprocess as cmd

smbus_present = 1

try:
    import smbus
except ImportError as e:
    smbus_present = 0


COPPER_TYPE = "COPPER"
SFP_TYPE = "SFP"

# SFP PORT numbers
SFP_PORT_START = 49
SFP_PORT_END = 54

SYSLOG_IDENTIFIER = "xcvrd"
sonic_logger = logger.Logger(SYSLOG_IDENTIFIER)


class Sfp(SfpBase):
    """Platform-specific Sfp class"""

    # Paths
    PLATFORM_ROOT_PATH = "/usr/share/sonic/device"
    PMON_HWSKU_PATH = "/usr/share/sonic/hwsku"
    HOST_CHK_CMD = "docker > /dev/null 2>&1"

    PLATFORM = "arm64-marvell_rd98DX35xx-r0"
    HWSKU = "rd98DX35xx"

    port_to_i2c_mapping = 0

    def __init__(self, index, sfp_type, eeprom_path, port_i2c_map):
        SfpBase.__init__(self)

        self.index = index
        self.port_num = index
        self.sfp_type = sfp_type
        self.eeprom_path = eeprom_path
        self.port_to_i2c_mapping = port_i2c_map
        self.port_name = sfp_type + str(index)
        self.port_to_eeprom_mapping = {}

        self.port_to_eeprom_mapping[index] = eeprom_path

    def __get_path_to_port_config_file(self):
        platform_path = "/".join([self.PLATFORM_ROOT_PATH, self.PLATFORM])
        hwsku_path = "/".join([platform_path, self.HWSKU]
                              ) if self.__is_host() else self.PMON_HWSKU_PATH
        return "/".join([hwsku_path, "port_config.ini"])

    def __read_eeprom_specific_bytes(self, offset, num_bytes):
        sysfsfile_eeprom = None

        eeprom_raw = []
        for i in range(0, num_bytes):
            eeprom_raw.append("0x00")

        sysfs_sfp_i2c_client_eeprom_path = self.port_to_eeprom_mapping[self.port_num]

        try:
            sysfsfile_eeprom = open(
                sysfs_sfp_i2c_client_eeprom_path, mode="rb", buffering=0)
            sysfsfile_eeprom.seek(offset)
            raw = sysfsfile_eeprom.read(num_bytes)
            for n in range(0, num_bytes):
                eeprom_raw[n] = hex(raw[n])[2:].zfill(2)
        except Exception as e:
            pass
        finally:
            if sysfsfile_eeprom:
                sysfsfile_eeprom.close()
        return eeprom_raw

    def get_reset_status(self):
        """
        Retrieves the reset status of SFP
        Returns:
            A Boolean, True if reset enabled, False if disabled
        """
        if self.sfp_type == COPPER_TYPE:
            return False

        if self.sfp_type == SFP_TYPE:
            return False

    def get_lpmode(self):
        """
        Retrieves the lpmode (low power mode) status of this SFP
        Returns:
            A Boolean, True if lpmode is enabled, False if disabled
        """
        if self.sfp_type == COPPER_TYPE:
            return False
        if self.sfp_type == SFP_TYPE:
            return False

    def get_power_override(self):
        """
        Retrieves the power-override status of this SFP
        Returns:
            A Boolean, True if power-override is enabled, False if disabled
        """
        if self.sfp_type == COPPER_TYPE:
            return False
        if self.sfp_type == SFP_TYPE:
            return False

    def get_tx_power(self):
        """
        Retrieves the TX power of this SFP
        Returns:
            A Boolean, True if reset enabled, False if disabled
        """
        if self.sfp_type == COPPER_TYPE:
            return False

        if self.sfp_type == SFP_TYPE:
            return False

    def reset(self):
        """
        Reset SFP.
        Returns:
            A boolean, True if successful, False if not
        """
        # RJ45 and SFP ports not resettable
        return False

    def tx_disable(self, tx_disable):
        """
        Disable SFP TX
        Args:
            tx_disable : A Boolean, True to enable tx_disable mode, False to disable
                         tx_disable mode.
        Returns:
            A boolean, True if tx_disable is set successfully, False if not
        """
        if self.sfp_type == COPPER_TYPE:
            return False

        if smbus_present == 0:  # if called from sfputil outside of pmon
            cmdstatus, register = cmd.getstatusoutput('sudo i2cget -y 0 0x76 0x8')
            if cmdstatus:
                sonic_logger.log_warning("sfp cmdstatus i2c get failed %s" % register )
                return False
            register = int(register, 16)
        else:
            bus = smbus.SMBus(0)
            DEVICE_ADDRESS = 0x76
            DEVICE_REG = 0x8
            register = bus.read_byte_data(DEVICE_ADDRESS, DEVICE_REG)

        pos = [1, 2, 4, 8, 16, 32]
        mask = pos[self.index-SFP_PORT_START]
        if tx_disable == True:
            setbits = register | mask
        else:
            setbits = register & ~mask

        if smbus_present == 0:  # if called from sfputil outside of pmon
            cmdstatus, output = cmd.getstatusoutput('sudo i2cset -y -m 0x0f 0 0x76 0x8 %d' % setbits)
            if cmdstatus:
                sonic_logger.log_warning("sfp cmdstatus i2c write failed %s" % output )
                return False
        else:
            bus = smbus.SMBus(0)
            DEVICE_ADDRESS = 0x76
            DEVICE_REG = 0x8
            bus.write_byte_data(DEVICE_ADDRESS, DEVICE_REG, setbits)

        return True

    def tx_disable_channel(self, channel, disable):
        """
        Sets the tx_disable for specified SFP channels
        Args:
            channel : A hex of 4 bits (bit 0 to bit 3) which represent channel 0 to 3,
                      e.g. 0x5 for channel 0 and channel 2.
            disable : A boolean, True to disable TX channels specified in channel,
                      False to enable
        Returns:
            A boolean, True if successful, False if not
        """

        return NotImplementedError

    def set_lpmode(self, lpmode):
        """
        Sets the lpmode (low power mode) of SFP
        Args:
            lpmode: A Boolean, True to enable lpmode, False to disable it
            Note  : lpmode can be overridden by set_power_override
        Returns:
            A boolean, True if lpmode is set successfully, False if not
        """
        if self.sfp_type == COPPER_TYPE:
            return False
        if self.sfp_type == SFP_TYPE:
            return False

    def set_power_override(self, power_override, power_set):
        """
        Sets SFP power level using power_override and power_set
        Args:
            power_override :
                    A Boolean, True to override set_lpmode and use power_set
                    to control SFP power, False to disable SFP power control
                    through power_override/power_set and use set_lpmode
                    to control SFP power.
            power_set :
                    Only valid when power_override is True.
                    A Boolean, True to set SFP to low power mode, False to set
                    SFP to high power mode.
        Returns:
            A boolean, True if power-override and power_set are set successfully,
            False if not
        """

        return NotImplementedError

    def get_name(self):
        """
        Retrieves the name of the device
            Returns:
            string: The name of the device
        """
        sfputil_helper = SfpUtilHelper()
        sfputil_helper.read_porttab_mappings(
            self.__get_path_to_port_config_file())
        name = sfputil_helper.logical[self.index-1] or "Unknown"
        return name

    def get_presence(self):
        """
        Retrieves the presence
        Returns:
            bool: True if is present, False if not
        """
        if self.sfp_type == COPPER_TYPE:
            return False

        if smbus_present == 0:  # if called from sfputil outside of pmon
            cmdstatus, sfpstatus = cmd.getstatusoutput('sudo i2cget -y 0 0x76 0xa')
            sfpstatus = int(sfpstatus, 16)
        else:
            bus = smbus.SMBus(0)
            DEVICE_ADDRESS = 0x76
            DEVICE_REG = 0xa
            sfpstatus = bus.read_byte_data(DEVICE_ADDRESS, DEVICE_REG)

        pos = [1, 2, 4, 8, 16, 32]
        bit_pos = pos[self.index-SFP_PORT_START]
        sfpstatus = sfpstatus & (bit_pos)

        if sfpstatus == 0:
            return True

        return False

    def get_status(self):
        """
        Retrieves the operational status of the device
        Returns:
            A boolean value, True if device is operating properly, False if not
        """
        return self.get_presence()

    def is_replaceable(self):
        """
        Indicate whether this device is replaceable.
        Returns:
            bool: True if it is replaceable.
        """

        if self.sfp_type == "SFP":
            return True
        else:
            return False

    def get_position_in_parent(self):
        """
        Retrieves 1-based relative physical position in parent device
        Returns:
            integer: The 1-based relative physical position in parent device
        """
        return self.index
