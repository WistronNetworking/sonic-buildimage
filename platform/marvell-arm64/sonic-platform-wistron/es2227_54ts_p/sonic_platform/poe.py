"""
Module contains an implementation of SONiC Platform Base API and
provides access to hardware poe
"""

import os
import subprocess

from sonic_platform_base.poe_base import PoeBase
from sonic_py_common import logger

sonic_logger = logger.Logger()


class PoeImplBase(PoeBase):
    """
    platform implementation for poe
    """

    lldp_pse_status_code = {
        '0': "Port is not deliver power",
        '1': "Port deliver power, using Layer1 (Assigned Class)",
        '2': "Port deliver power, Using Layer1 Autoclass",
        '3': "Port deliver power, using LLDP",
        '4': "Port deliver power, using LLDP Autoclass",
        '5': "Port deliver power, using CDP over 2P",
        '6': "Port deliver power, using CDP over 4P",
        '7': "Port is not deliver power and at reserve mode",
        '8': "Port deliver power, using reserve mode power"
    }
    def __init__(self):

        self.tool_path = "/usr/local/bin/poetool"

    def get_priority_value(self, priority):
        priority_mapping = {
            "critical": "1",
            "high": "2",
            "low": "3",
            "0xff": "0xff"  # 0xff means don't change the setting
        }
        return priority_mapping.get(priority, None)

    def parse_data(self, data):
        lines = data.strip().split('\n')
        parsed_data = {}
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 2:
                key = parts[0]
                value = " ".join(parts[1:])
                parsed_data[key] = value
        return parsed_data

    def parse_keyword(self, keyword, string):
        if string:
            # Parse the value of keyword from the string
            index = string.find(keyword)
            if index != -1:
                start_index = index + len(keyword)
                end_index = string.find("\n", start_index)
                result = string[start_index:end_index].strip()
            else:
                print("Unable to find the result in the output.")
                return None

            return result
        else:
            return None


    def parse_poe_status(self, poe_status):
        """Parse and return the PoE status."""
        DELIVERING_START = 0x80
        DELIVERING_END = 0x89
        POE_OPEN = 0xA8
        PORT_OFF = 0x1A
        PORT_UNDERLOAD = 0x1E
        PORT_OVERLOAD = 0x1F
        PORT_EXCEED_BUDGET = 0x20
        PORT_EXCEED_LIMIT = 0x3C

        if (int(poe_status) >= DELIVERING_START and int(poe_status) <= DELIVERING_END):
            poe_status = "delivering"
        elif (int(poe_status) == POE_OPEN):
            poe_status = "open"
        elif (int(poe_status) == PORT_OFF):
            poe_status = "off"
        elif (int(poe_status) == PORT_UNDERLOAD):
            poe_status = "underload"
        elif (int(poe_status) == PORT_OVERLOAD):
            poe_status = "overload"
        elif (int(poe_status) == PORT_EXCEED_BUDGET):
            poe_status = "> power budget"
        elif (int(poe_status) == PORT_EXCEED_LIMIT):
            poe_status = "> power limit"
        else:
            poe_status = "State {}".format(hex(int(poe_status)))

        return poe_status

    def run_command(self, command):
        try:
            output = subprocess.check_output(
                command, shell=True, stderr=subprocess.STDOUT, universal_newlines=True)
        except subprocess.CalledProcessError as e:
            return None, None

        if output:
            # Parse the value of "result" from the output
            keyword = "result "
            index = output.find(keyword)
            if index != -1:
                start_index = index + len(keyword)
                end_index = output.find("\n", start_index)
                result = output[start_index:end_index].strip()
            else:
                print("Unable to find the result in the output.")
                return None, None

        # print("output: {}\n".format(output.strip()))
        return result, output.strip()

    def get_total_power(self):
        """
        Retrieves current total power reading from poe chip

        Returns:
            A dict which contains following keys/values :
        ================================================================================
        keys                        Value Format    Information
        --------------------------- --------------- ----------------------------
        powerconsumption            1*255VCHAR       power consumption
        calcpower                   1*255VCHAR       calculate power
        availablepower              1*255VCHAR       available power
        powerlimit                  1*255VCHAR       power limit
        powerbank                   1*255VCHAR       power bank
        """
        power_key = {'powerconsumption', 'calcpower', 'availablepower', 'powerlimit', 'powerbank'}
        total_power_info_dict = dict.fromkeys(power_key, "NA")

        cmd = "sudo {} mgmt get_total_power".format(self.tool_path)

        result, output_str = self.run_command(cmd)
        if result == None:
            result, output_str = self.run_command(cmd)

        if result != None:
            keyword = "powerConsumption"
            total_power_info_dict['powerconsumption'] = self.parse_keyword(keyword, output_str)
            keyword = "calcPower"
            total_power_info_dict['calcpower'] = self.parse_keyword(keyword, output_str)
            keyword = "availablePower"
            total_power_info_dict['availablepower'] = self.parse_keyword(keyword, output_str)
            keyword = "powerLim"
            total_power_info_dict['powerlimit'] = self.parse_keyword(keyword, output_str)
            keyword = "powerBank"
            total_power_info_dict['powerbank'] = self.parse_keyword(keyword, output_str)

        return total_power_info_dict

    def save_config(self):
        """
        save the poe chip configuration

        Returns:
            A boolean, True if configuration saved successfully, False if not
        """
        cmd = "sudo {} system save_setting".format(self.tool_path)
        result, output_str = self.run_command(cmd)

        return False if result == None else True

    def get_port_status(self, port_num):
        """
        Retrieves the port poe status like port status/port power consumption

        Args :
            port_num: A interger number for the poe port want to check

        Returns:
            A dict which contains following keys/values :
        ================================================================================
        keys                        Value Format    Information
        --------------------------- --------------- ----------------------------
        portstatus                  1*255VCHAR       status code of this port
        assignedClassPrimary        1*255VCHAR       assigned class for primary
        assignedClassSecondary      1*255VCHAR       assigned class for secondary
        measuredPortPower           1*255VCHAR       value of the power measure
        """
        ps_key = {'portstatus', 'assignedClassPrimary', 'assignedClassSecondary', 'measuredPortPower'}
        port_status_info_dict = dict.fromkeys(ps_key, "NA")

        cmd = "sudo %s port get_port_status %s" % (self.tool_path, port_num)
        result, output_str = self.run_command(cmd)
        if result == None:
            result, output_str = self.run_command(cmd)

        if result != None:
            parsed_data = self.parse_data(output_str)
            poe_status = parsed_data.get('portStatus')
            poe_class_p = parsed_data.get('assignedClassPrimary')
            poe_class_s = parsed_data.get('assignedClassSecondary')
            poe_measure_power = parsed_data.get('measuredPortPower')
            poe_measure_power = float(poe_measure_power)/10

            if poe_class_p == '12':
                poe_class_p = '-'
            if poe_class_s == '12':
                poe_class_s = '-'
            poe_status = self.parse_poe_status(poe_status)

            port_status_info_dict['portstatus'] = poe_status
            port_status_info_dict['assignedClassPrimary'] = poe_class_p
            port_status_info_dict['assignedClassSecondary'] = poe_class_s
            port_status_info_dict['measuredPortPower'] = str(poe_measure_power)


        return port_status_info_dict

    def get_port_lldp_pse_data(self, port_num):
        """
        Retrieves the port poe lldp pse data

        Args :
            port_num: A interger number for the poe port want to check

        Returns:
            A dict which contains following keys/values :
        ================================================================================
        keys                        Value Format    Information
        --------------------------- --------------- ----------------------------
        layer2Usage                 1*255VCHAR       lldp status code of this port
        """
        lldp_pse_key = {'layer2Usage'}
        lldp_pse_info_dict = dict.fromkeys(lldp_pse_key, "NA")

        cmd = "sudo %s port get_lldp_pse_data %s" % (self.tool_path, port_num)
        result, output_str = self.run_command(cmd)
        if result == None:
            result, output_str = self.run_command(cmd)

        if result != None:
            parsed_data = self.parse_data(output_str)
            poe_l2_usage = parsed_data.get('layer2Usage')

            lldp_pse_info_dict['layer2Usage'] = self.lldp_pse_status_code[poe_l2_usage]

        return lldp_pse_info_dict

    def set_port_reserve_power(self, port_num, power):
        """
        Sets the reserved power of port

        Args:
            port_num:  A interger number for the poe port want to set
            power: A float number for the reserve power, e.g. 29.5
        Returns:
            A boolean, True if configuration saved successfully, False if not
        """
        cmd = "sudo %s port set_port_reserve_power %s %s 1" % (self.tool_path, port_num, int(power*10))
        result, output_str = self.run_command(cmd)

        return False if result == None else True

    def set_port_power(self, port_num, power_class, power_mode, power):
        """
        Sets the power control mode and value of port

        Args:
            port_num:  A interger number for the poe port want to set
            power_class:  A string of the power class
            port_mode:  A string for the power mode
            power: A float number for the reserve power, e.g. 29.5
        Returns:
            A boolean, True if configuration saved successfully, False if not
        """
        pure_power_mode = "enable" if (power_mode == "enableDynamic" or
                                   power_mode == "enableStatic") else "disable"

        pure_power_calculation = "static" if (power_mode == "enableStatic" or
                                          power_mode == "disableStatic") else "disable"

        cfg1 = 1 if pure_power_mode == "enable" else 0
        cfg2 = 1 if pure_power_calculation == "static" else 0
        oper_mode = "9" if power_class == "AT" else "0"

        if (pure_power_mode == "enable" and power != 0):
            oper_mode = 0x31  # According to 3.3.12 Set BT Port Reserve Power Request

        cmd = "sudo %s port set_port_params %s %s %s %s 0 0xff" % (self.tool_path, port_num, cfg1, cfg2, oper_mode)
        result, output_str = self.run_command(cmd)

        if (pure_power_mode == "enable" and power != 0 and result != None):
            return self.set_port_reserve_power(port_num, power)


        return False if result == None else True

    def set_port_priority(self, port_num, priority):
        """
        Sets the poe priority of port

        Args:
            port_num:  A interger number for the poe port want to set
            priority:  A string of the priority
        Returns:
            A boolean, True if configuration saved successfully, False if not
        """
        pri = self.get_priority_value(priority)
        cmd = "sudo %s port set_port_params %s 0xf 0xff 0xff 0xff %s" % (self.tool_path, port_num, pri)
        result, output_str = self.run_command(cmd)

        return False if result == None else True

    def set_preemptive_priority(self, state):
        """
        Sets the preemptive priority of poe chip

        Args:
            state:  enable or disable for preemptive priority
        Returns:
            A boolean, True if configuration saved successfully, False if not
        """
        IGNORANCE_MASK = '0'
        value = 0 if state == 'enable' else 1
        cmd = "sudo %s system set_idv_mask %s %s" % (self.tool_path, IGNORANCE_MASK, value)
        result, output_str = self.run_command(cmd)

        return False if result == None else True

    def set_power_redundant(self, state):
        """
        Sets the power redundant mode of system

        Args:
            state:  enable or disable for power redundant mode of system
        Returns:
            A boolean, True if configuration saved successfully, False if not
        """
        result = None
        FILE_PATH = "/sys/bus/i2c/devices/0-0033/psu_budget_mode"

        if not os.path.exists(FILE_PATH):
            print("FILE_PATH is not exsiting")
            return False
        value = 0 if state == 'enable' else  1
        cmd = "sudo echo  %s >> %s" % (value, FILE_PATH)
        try:
            result = subprocess.check_output(
                cmd, shell=True, stderr=subprocess.STDOUT, universal_newlines=True)
            # print(result.strip())
        except subprocess.CalledProcessError as e:
            print("Error running poe set power redundant:", e)
            return False

        return False if result == None else True

    def set_port_lldp_pd_req(self, port_num, req_power):
        """
        Sets the power redundant mode of system

        Args:
            port_num:  A interger number for the poe port want to set
            power: A int number from lldpd for power device request(unit: milliwalt)
        Returns:
            A boolean, True if configuration saved successfully, False if not
        """
        power = int(req_power/100) #microchip power unit is 0.1W
        cmd = "sudo %s port set_lldp_pd_req %s %s 0 0 0 0 0xff" % (self.tool_path, port_num, power)
        result, output_str = self.run_command(cmd)

        return False if result == None else True

    def chip_reset(self):
        """
        Sets the power redundant mode of system

        Returns:
            A boolean, True if run chip reset successfully, False if not
        """
        cmd = "sudo {} system reset".format(self.tool_path)
        result, output_str = self.run_command(cmd)

        return False if result == None else True

