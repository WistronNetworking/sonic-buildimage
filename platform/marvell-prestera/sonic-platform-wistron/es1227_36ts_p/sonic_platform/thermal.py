#!/usr/bin/env python

#############################################################################
#
# Thermal contains an implementation of SONiC Platform Base API and
# provides the thermal device status which are available in the platform
#
#############################################################################

import os
import subprocess

try:
    from sonic_platform.sfp import Sfp
    from sonic_platform_base.thermal_base import ThermalBase
except ImportError as e:
    raise ImportError(str(e) + " - required module not found")


class Thermal(ThermalBase):
    """Platform-specific Thermal class"""

    def __init__(self, thermal_index):
        self.index = thermal_index

        # Sensor names
        self.THERMAL_NAME_LIST = [
            "XFMR Ambient",    #0
            "DDR Ambient",     #1
            "System Ambient",  #2
            "CPU Temp",        #3
            "Dimm Temp",       #4
            "MAC Temp",        #5
            "PoE Temp",        #6
            "XCVR 1 Temp",     #7
            "XCVR 2 Temp",     #8
            "XCVR 3 Temp",     #9
            "XCVR 4 Temp"      #10
        ]

        # SYSFS paths for sensors
        self.SYSFS_THERMAL_DIR = [
            "/sys/bus/i2c/devices/2-004a/hwmon/",  # XFMR Ambient
            "/sys/bus/i2c/devices/2-0049/hwmon/",  # System Ambient
            "/sys/bus/i2c/devices/2-004b/hwmon/",  # SDR/DIMM Ambient
            "/sys/devices/virtual/thermal/thermal_zone1/",  # CPU Temp
            "/sys/bus/i2c/devices/0-001b/hwmon/"  # DDR DIMM Temp
        ]
        self.POE_TEMP_FILE = "/tmp/poe_temp"

        if thermal_index >= 7:
            self.sfp_module = Sfp(33 + (thermal_index - 7), 'SFP')

        ThermalBase.__init__(self)
        self.minimum_thermal = 150.0
        self.maximum_thermal = 0.0

    def __read_txt_file(self, file_path):
        """Reads content of a text file at 'file_path'."""
        try:
            with open(file_path, 'r') as fd:
                return fd.read().strip()
        except IOError:
            return ""

    def __get_temp(self, temp_file):
        """Fetch the temperature dynamically."""
        print(f'self.index [{self.index}]')
        if self.index == 6:  # Index for PoE Temp - Use external command
            try:
                # Uncomment required implementation based on requirement:

                # Option 1: Using /tmp/poe_temp file
                raw_temp = self.__read_txt_file(self.POE_TEMP_FILE)
                print(f'[1] raw_temp [{raw_temp}]')
                if raw_temp and raw_temp.isdigit():
                    return float(raw_temp)

                # Option 2: Using external command (poetool)
                command = "poetool device get_dev_status 0 | grep temperature | awk '{printf $2}'"
                raw_temp = subprocess.check_output(command, shell=True, text=True).strip()
                print(f'[2] raw_temp [{raw_temp}]')
                if raw_temp.isdigit():
                    return float(raw_temp)

            except subprocess.CalledProcessError as e:
                print(f"Failed to read PoE temperature: {str(e)}")
            except Exception as e:
                print(f"Unexpected error while reading PoE temperature: {str(e)}")
            return None

        # General handling for other sensors
        elif self.index < len(self.SYSFS_THERMAL_DIR):
            temp_dir = self.SYSFS_THERMAL_DIR[self.index]
            hwmon_dir = next(
                (d for d in os.listdir(temp_dir) if d.startswith("hwmon")), '')
            temp_file_path = os.path.join(temp_dir, hwmon_dir, temp_file)

            raw_temp = self.__read_txt_file(temp_file_path)
            if raw_temp and raw_temp.isdigit():
                return float(raw_temp) / 1000
        return None

    def get_temperature(self):
        """Retrieve the temperature corresponding to the current index."""
        if self.index == 6:  # PoE Temp
            return self.__get_temp(None)

        if self.index < len(self.SYSFS_THERMAL_DIR):
            temp_file = "temp1_input" if self.index != 3 else "temp"  # CPU temp uses "temp"
            return self.__get_temp(temp_file)
        return None

    def get_high_threshold(self):
        """Retrieve high thresholds."""
        thresholds = {
            0:  80.0,  # XFMR Ambient
            1:  80.0,  # DDR Ambient
            2:  80.0,  # System Ambient
            3:  90.0,  # CPU Temp
            4:  85.0,  # DDR Temp
            5: 110.0,  # MAC Temp
            6: 100.0,  # PoE Temp
        }
        return thresholds.get(self.index, 68.0)  # Default = 68.0

    def get_high_critical_threshold(self):
        """Retrieve the high critical threshold temperature of thermal."""
        thresholds_critical = {
            0: 85.0,  # XFMR Ambient
            1: 85.0,  # DDR Ambient
            2: 80.0,  # System Ambient
            3: 95.0,  # CPU Temp
            4: 88.0,  # Dimm Temp
            5: 120.0,  # MAC Temp
            6: 108.0,  # PoE Temp
        }
        return thresholds_critical.get(self.index, 75.0)

    def get_low_threshold(self):
        """Retrieve low thresholds dynamically."""
        return 2.0

    def get_low_critical_threshold(self):
        """Retrieve the low critical threshold temperature of thermal."""
        return 0.0

    def get_model(self):
        """Retrieve the model number (or part number) of the device."""
        return "Unknown Model"

    def get_serial(self):
        """Retrieve the serial number of the device."""
        return "Unknown Serial"

    def get_minimum_recorded(self):
        """Retrieve the minimum recorded temperature of thermal."""
        current_temp = self.get_temperature()
        if current_temp is not None and current_temp < self.minimum_thermal:
            self.minimum_thermal = current_temp
        return self.minimum_thermal

    def get_maximum_recorded(self):
        """Retrieve the maximum recorded temperature of thermal."""
        current_temp = self.get_temperature()
        if current_temp is not None and current_temp > self.maximum_thermal:
            self.maximum_thermal = current_temp
        return self.maximum_thermal

    def get_presence(self):
        """Check if the sensor is present."""
        if self.index == 6:  # PoE Temp
            return os.path.isfile(self.POE_TEMP_FILE)

        if self.index < len(self.SYSFS_THERMAL_DIR):
            temp_file = "temp1_input" if self.index != 3 else "temp"
            temp_dir = self.SYSFS_THERMAL_DIR[self.index]
            hwmon_dir = next(
                (d for d in os.listdir(temp_dir) if d.startswith("hwmon")), '')
            temp_file_path = os.path.join(temp_dir, hwmon_dir, temp_file)
            return os.path.isfile(temp_file_path)
        return False

    def get_position_in_parent(self):
        """Retrieve position in parent device."""
        return self.index + 1

    def get_status(self):
        """Retrieve the operational status of the device."""
        return self.get_presence()

    def get_name(self):
        """Retrieve the name of the thermal device."""
        if self.index < len(self.THERMAL_NAME_LIST):
            return self.THERMAL_NAME_LIST[self.index]
        return "Unknown"

    def is_replaceable(self):
        """Retrieve whether thermal module is replaceable."""
        return False

    def __str__(self):
        """For debugging: return the sensor name and temperature."""
        return f"Sensor: {self.get_name()}, Temperature: {self.get_temperature()} C"
