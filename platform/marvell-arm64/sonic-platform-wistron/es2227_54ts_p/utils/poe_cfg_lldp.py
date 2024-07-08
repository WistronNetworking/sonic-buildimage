#!/usr/bin/env python

import os
import subprocess
import json
import time
from swsscommon.swsscommon import ConfigDBConnector
from sonic_py_common import logger

SYSLOG_IDENTIFIER = "poelldp"
log = logger.Logger(SYSLOG_IDENTIFIER)

# Global dictionary to store power data for each port
port_power_data = {}
allocated_values = {}
requested_values = {}
lldp_power_sm = {}

def run_lldpcli_neighbor_command():
    try:
        result = subprocess.run(['lldpcli', '-f', 'json', 'show',
                                'ne', 'de'], capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print("Error running run_lldpcli_neighbor_command command:", e)
        return None


def run_lldpcli_config_port_command(interface, pri, req, alloc):
    # lldpcli configure port Etherent40 dot3 power pse supported enabled powerpairs signal class class-4 type 2  source primary priority low requested 28000 allocated 15000

    cmd = "lldpcli configure port %s dot3 power pse supported enabled powerpairs signal class class-4 type 2  source primary priority %s requested %s allocated %s" % (
        interface, pri, req, alloc)
    # print(cmd)
    try:
        result = subprocess.check_output(
            cmd, shell=True, stderr=subprocess.STDOUT, universal_newlines=True)
        # print(result.strip())
        return result.strip()
    except subprocess.CalledProcessError as e:
        print("Error running run_lldpcli_neighbor_command command:", e)
        return None


def run_command(command):
    try:
        output = subprocess.check_output(
            command, shell=True, stderr=subprocess.STDOUT, universal_newlines=True)
    except subprocess.CalledProcessError as e:
        print("Command failed:")
        print(e.output)
        return None, None

    if output:
        # Parse the value of "result" from the output
        keyword = "result "
        index = output.find(keyword)
        if index != -1:
            start_index = index + len(keyword)
            end_index = output.find("\n", start_index)
            result = output[start_index:end_index].strip()
            # click.echo("result: {}\n".format(result))
        else:
            print("Unable to find the result in the output.")
            return None, None

    # click.echo("output: {}\n".format(output.strip()))
    return result, output.strip()


def run_port_lldp_pd_req_cmd(port_num, single, dual_a, dual_b, auto_class, cable_len, l2cfg):

    # Here is the command to set port params
    # poetool port set_lldp_pd_req <PortNum> <PDReqPowerSingle> <PDReqPowerDualA> <PDReqPowerDualB> <Autoclass> <CableLength> <L2CFG>

    cmd = "sudo poetool port set_lldp_pd_req %s %s %s %s %s %s %s" % (
        port_num, single, dual_a, dual_b, auto_class, cable_len, l2cfg)
    # print(cmd)
    result, output_str = run_command(cmd)
    # click.echo("result: {}\n".format(result))
    return result, output_str


def run_port_port_status_cmd(port_num):

    # Here is the command to get port poe status
    # admin@sonic:/usr/local/bin$ sudo poetool port get_lldp_pse_data 2
    # Code 69 get_lldp_pse_data, result 0
    # pseAllocatedPwrSingleOrAltA  255
    # pseAllocatedPwrAltB  0
    # pseMaxPwr  300
    # assignedClassPrimary  4
    # assignedClassSecondary  12
    # layer2Execution  0
    # layer2Usage  1
    # ieeeBTPwrBitsExt1514  1
    # ieeeBTPwrBitsExt1110  1
    # cableLength  10
    # layer2_cfg  1

    cmd = "sudo poetool port get_lldp_pse_data %s" % (port_num)
    # print(cmd)
    result, output_str = run_command(cmd)
    # click.echo("result: {}\n".format(result))
    return result, output_str


def parse_keyword(keyword, string):  # James
    if string:
        # Parse the value of keyword from the string
        index = string.find(keyword)
        if index != -1:
            start_index = index + len(keyword)
            end_index = string.find("\n", start_index)
            result = string[start_index:end_index].strip()
            # click.echo("result: {}\n".format(result))
        else:
            print("Unable to find the result in the output.")
            return None

        # click.echo("string: {}\n".format(string.strip()))
        return result
    else:
        return None

def get_priority_value(priority):  # James
    priority_mapping = {
        "critical": "1",
        "high": "2",
        "low": "3",
        "0xff": "0xff"  # 0xff means don't change the setting
    }
    return priority_mapping.get(priority, None)

def run_lldpcli_show_port_command(interface):
    # lldpcli configure port Etherent40 dot3 power pse supported enabled powerpairs signal class class-4 type 2  source primary priority low requested 28000 allocated 15000

    cmd = "lldpcli show in port %s de" % (interface)
    # print(cmd)
    try:
        result = subprocess.check_output(
            cmd, shell=True, stderr=subprocess.STDOUT, universal_newlines=True)
        # print(result.strip())
        return result.strip()
    except subprocess.CalledProcessError as e:
        log.log_error("Error running run_lldpcli_neighbor_command command:", e)
        return None

def run_lldpcli_init_port_command(interface, enabled):
    # lldpcli configure port Etherent40 dot3 power pse supported enabled powerpairs signal class class-4 type 2  source primary priority low requested 28000 allocated 15000

    cmd = "lldpcli configure port %s dot3 power pse supported %s powerpairs signal class class-4" % (
        interface, enabled)
    # print(cmd)
    try:
        result = subprocess.check_output(
            cmd, shell=True, stderr=subprocess.STDOUT, universal_newlines=True)
        # print(result.strip())
        return result.strip()
    except subprocess.CalledProcessError as e:
        log.log_error("Error running run_lldpcli_neighbor_command command:", e)
        return None

def set_preemptive(enabled):
    IGNORANCE_MASK = '0'
    value = 0 if enabled else 1
    cmd = "sudo poetool system set_idv_mask %s %s" % (IGNORANCE_MASK, value)
    result, output_str = run_command(cmd)
    if (result == None or int(result) != 0):
        result, output_str = run_command(cmd)
        log.log_error("try it again: " + result)

def set_redundant(enabled):
    value = 0 if enabled else 1
    FILE_PATH = "/sys/bus/i2c/devices/0-0033/psu_budget_mode"
    if not os.path.exists(FILE_PATH):
        log.log_error("FILE_PATH is not exsiting " + FILE_PATH)

    cmd = "sudo echo  %s >> %s" % (value, FILE_PATH)
    try:
        result = subprocess.check_output(
            cmd, shell=True, stderr=subprocess.STDOUT, universal_newlines=True)
        # print(result.strip())
    except subprocess.CalledProcessError as e:
        log.log_error("Error running run_power_redundant_cmd command:" + e)

def poe_cfg():
    # PoE port configuration# Wait for the file to be ready
    CONFIG_DB_FILE = '/etc/sonic/config_db.json'
    while not os.path.exists(CONFIG_DB_FILE):
        print(f"Waiting for {CONFIG_DB_FILE} to be ready...")
        time.sleep(3)  # Sleep for 3 second before checking again

    with open(CONFIG_DB_FILE) as json_file:
        # Load the contents of the file
        data = json.load(json_file)

    poe_in_config_db = True if 'POE' in data else False

    # Check if poe cfg not in the config_db
    if poe_in_config_db == False:
        # If poe cfg not in the config_db yet
        # 1. Use the default poe cfg json file
        # 2. Add the default poe cfg to redis DB

        # Use the default poe cfg json file
        CONFIG_POE_DB_FILE = '/usr/share/sonic/device/arm64-wistron_es2227_54ts_p-r0/wistron_es2227_54ts_p/poe_default_cfg.json'
        with open(CONFIG_POE_DB_FILE) as json_file:
            # Load the contents of the file
            data = json.load(json_file)
        poe_dict = data['POE']

        # Add the default poe cfg to redis DB
        config_db = ConfigDBConnector()
        config_db.connect()
        for key in poe_dict:
            if key == 'Global':
                config_db.set_entry("POE", key, {'preemptive_priority': poe_dict[key]["preemptive_priority"]})
                config_db.set_entry("POE", key, {'power_redundant': poe_dict[key]["power_redundant"]})
            else:
                config_db.set_entry("POE", key, {'lanes': poe_dict[key]["lanes"]})
                config_db.set_entry("POE", key, {'priority': poe_dict[key]["priority"]})
                config_db.set_entry("POE", key, {'power_mode': poe_dict[key]["power_mode"]})
                config_db.set_entry("POE", key, {'maxpower': poe_dict[key]["maxpower"]})
                config_db.set_entry("POE", key, {'class': poe_dict[key]["maxpower"]})
                config_db.set_entry("POE", key, {'lldp': poe_dict[key]["lldp"]})
    else:
        poe_dict = data['POE']

    for key in poe_dict:
        if key == 'Global': #Global setting
            preemptive = False if poe_dict[key]["preemptive_priority"] == 'disable' else True
            set_preemptive(preemptive)

            redundant = False if poe_dict[key]["power_redundant"] == 'disable' else True
            set_redundant(redundant)

        if poe_dict[key]["priority"] == "NA":
            continue  # Skips the non-poe ports

        # command = "poeutil port-priority %s %s" % (
        #    key, port_dict[key]["poe_pri"])
        # clicommon.run_command(command)
        # command = "poeutil power-inline %s %s %s" % (
        #    key, port_dict[key]["poe_status"], port_dict[key]["poe_maxpower"])
        # clicommon.run_command(command)

        port_num = poe_dict[key]["lanes"]
        power_mode = poe_dict[key]["power_mode"]
        max_power = int(float(poe_dict[key]["maxpower"]) * 10)
        poe_class = poe_dict[key]["class"]

        pure_power_mode = "enable" if (power_mode == "enableDynamic" or
                                       power_mode == "enableStatic" or
                                       power_mode == "enable") else "disable"

        pure_power_calculation = "static" if (power_mode == "enableStatic" or
                                              power_mode == "disableStatic") else "disable"

        cfg2 = 1 if pure_power_calculation == "static" else 0

        oper_mode = "9" if poe_class == 'AT' else "0"
        add_power = 0

        if (pure_power_mode == "enable" and int(max_power) != 0):
            cfg1 = 1
            oper_mode = 0x31  # According to 3.3.12 Set BT Port Reserve Power Request
        elif (pure_power_mode == "enable"):
            cfg1 = 1
        elif (pure_power_mode == "disable"):
            cfg1 = 0

        pri = get_priority_value(poe_dict[key]["priority"])

        cmd = "poetool port set_port_params %s %s %s %s %s %s" % (
            port_num, cfg1, cfg2, oper_mode, add_power, pri)
        result, output_str = run_command(cmd)
        if (result == None or int(result) != 0):
            result, output_str = run_command(cmd)
            log.log_error("try it again: " + result)

        if (pure_power_mode == "enable" and int(max_power) != 0):
            reserve_power = max_power
            pse_type = 1
            cmd = "poetool port set_port_reserve_power %s %s %s" % (
                port_num, reserve_power, pse_type)
            result, output_str = run_command(cmd)
            if (result == None or int(result) != 0):
                result, output_str = run_command(cmd)
                log.log_error("try it again: " + result)

            # print("cmd: ")
            # print(cmd)
            # print("result: ")
            # print(result)

        # print(key, port_dict[key]["poe_pri"])

    # Save setting and rest to make poe priority work properly
    time.sleep(1)
    cmd = "poetool system save_setting"
    result, output_str = run_command(cmd)
    if (result == None or int(result) != 0):
        result, output_str = run_command(cmd)
        log.log_error("try it again: " + result)
    time.sleep(2)
    cmd = "poetool system reset"
    result, output_str = run_command(cmd)
    if (result == None or int(result) != 0):
        result, output_str = run_command(cmd)
        log.log_error("try it again: " + result)

    while True:
        key = "Ethernet0"
        result = run_lldpcli_show_port_command(key)
        # print(result)
        match = parse_keyword(key, result)

        if match is not None:
            # print("Function returned:", result)
            time.sleep(2)
            break
        else:
            # print("Function returned None. Retrying in 5 seconds...")
            time.sleep(5)

    for key in poe_dict:
        if key == 'Global': #Global setting
            continue  # Skips
        if poe_dict[key]["priority"] == "NA":
            continue  # Skips the non-poe ports

        state = poe_dict[key]["lldp"]
        # print(state)
        enabled = 'enabled' if (state == 'enable') else ''

        while True:
            result = run_lldpcli_init_port_command(key, enabled)
            # print(result)
            if result is not None:
                # print("Function returned:", result)
                break
            else:
                # print("Function returned None. Retrying in 5 seconds...")
                time.sleep(5)

def parse_lldpcli_output(output):
    try:
        # open DB
        config_db = ConfigDBConnector()
        config_db.connect()
        poe_dict = config_db.get_table('POE')

        lldp_data = json.loads(output)
        interfaces = lldp_data['lldp']['interface']

        # print("lldp_debug: {}".format(lldp_debug))

        # Store dictionary keys in a list
        key_list = list(lldp_power_sm.keys())

        for key in key_list:
            if (lldp_debug != "0"):
                print("key: {} value: {}".format(key, lldp_power_sm[key]))
            is_found = 0
            for interface in interfaces:
                # print("interface: {}".format(interface))a
                if key in interface:
                    # print("xxxx")
                    is_found = 1
                    break
            # reset all parameters
            if (is_found == 0):
                del lldp_power_sm[key]
                if key in allocated_values:
                    del allocated_values[key]
                if key in requested_values:
                    del requested_values[key]
                if (lldp_debug != "0"):
                    print("reset intf: {}".format(key))

        for interface in interfaces:
            if (lldp_debug != "0"):
                # print(interface)
                pass
            for intf in interface:
                if (lldp_debug != "0"):
                    print(intf)
                if intf not in poe_dict:
                    if (lldp_debug != "0"):
                        print("intf not in poe intf")
                    pass
                elif intf == "eth0":
                    if (lldp_debug != "0"):
                        print("intf == eth0")
                    pass
                elif poe_dict[intf]["power_mode"] != "enableDynamic" and poe_dict[intf]["power_mode"] != "enableStatic":
                    if (lldp_debug != "0"):
                        print("poe_dict[intf][power_mode] is not eanble")
                    pass  # Skip the poe-disabled ports
                elif poe_dict[intf]["lldp"] != "enable":
                    if (lldp_debug != "0"):
                        print("poe_dict[intf][lldp] is not eanble")
                    pass  # Skip the poe_tlv-disabled ports
                elif poe_dict[intf]["maxpower"] != "0" and poe_dict[intf]["maxpower"] != "0.0":
                    if (lldp_debug != "0"):
                        print("poe_dict[intf][poe_maxpower] is not zero")
                    pass  # Skip the static-power ports
                else:
                    if 'port' not in interface[intf]:
                        if (lldp_debug != "0"):
                            print("port not in interface[intf]")
                        pass

                        port_power_data[intf] = {
                            'supported': False,
                            'enabled': False,
                            'device-type': '',
                            'paircontrol': False,
                            'pairs': '',
                            'class': '',
                            'power-type': '',
                            'source': '',
                            'priority': '',
                            'requested': '0',
                            'allocated': ''
                        }
                    elif 'power' not in interface[intf]['port']:
                        if (lldp_debug != "0"):
                            print("power not in interface[intf]")
                        pass

                        port_power_data[intf] = {
                            'supported': False,
                            'enabled': False,
                            'device-type': '',
                            'paircontrol': False,
                            'pairs': '',
                            'class': '',
                            'power-type': '',
                            'source': '',
                            'priority': '',
                            'requested': '0',
                            'allocated': ''
                        }
                    else:
                        # print(interface)
                        port_data = interface[intf]['port']['power']
                        # print("port_data: {}", port_data)
                        port_power_data[intf] = {
                            'supported': port_data.get('supported', False),
                            'enabled': port_data.get('enabled', False),
                            'device-type': port_data.get('device-type', ''),
                            'paircontrol': port_data.get('paircontrol', False),
                            'pairs': port_data.get('pairs', ''),
                            'class': port_data.get('class', ''),
                            'power-type': port_data.get('power-type', ''),
                            'source': port_data.get('source', ''),
                            'priority': port_data.get('priority', ''),
                            'requested': port_data.get('requested', '0'),
                            'allocated': port_data.get('allocated', '')
                        }

                    if (port_power_data[intf]['requested'] == '0'):
                        # no requested power, nothing to do
                        if (lldp_debug != "0"):
                            print("power reuqest is zero")
                        pass

                        # Check the poe attribute in lldp-med
                        if 'lldp-med' not in interface[intf]:
                            if (lldp_debug != "0"):
                                print("lldp-med not in interface[intf]")
                            continue
                        elif 'poe' not in interface[intf]['lldp-med']:
                            if (lldp_debug != "0"):
                                print("lldp-med not in interface[intf]")
                            continue
                        else:
                            lldp_med_poe = interface[intf]['lldp-med']['poe']

                            port_power_data[intf]['device-type'] = lldp_med_poe['device-type']
                            port_power_data[intf]['requested'] = lldp_med_poe['power']

                    port_name = intf
                    single = int(
                        int(port_power_data[intf]['requested'])/100)

                    if port_power_data[intf]['device-type'] != 'PD':
                        if (lldp_debug != "0"):
                            print("not a PD")
                        pass  # Support PD only as the switch is a PSE
                    elif intf in allocated_values and allocated_values[intf] == port_power_data[intf]['requested']:
                        if (lldp_debug != "0"):
                            print(" Bingo: {}", intf)
                        pass  # The request has been handled
                        # log.log_error(" Bingo: {}", intf)
                    elif intf in requested_values and requested_values[intf] == port_power_data[intf]['requested'] and lldp_power_sm[intf] == 0:
                        if (lldp_debug != "0"):
                            print(" Bingo2: {}".format(intf))
                        pass  # The request has been handled
                        # log.log_error(" Bingo2: {}", intf)
                    else:
                        # 1. call poetool to requst power
                        requested_values[intf] = port_power_data[intf]['requested']
                        if (intf not in lldp_power_sm or lldp_power_sm[intf] == 0):
                            # print("0xffff")
                            lldp_power_sm[intf] = 1
                            port_name = intf
                            # the requested power in lldpd is milliwatt(0.001w) and in poetool is 0.1w
                            single = int(
                                int(port_power_data[intf]['requested'])/100)
                            dual_a = 0
                            dual_b = 0
                            auto_class = 0
                            cable_len = 0
                            l2cfg = 0xff

                            result, output_str = run_port_lldp_pd_req_cmd(
                                poe_dict[intf]["lanes"], single, dual_a, dual_b, auto_class, cable_len, l2cfg)

                            if result is None or int(result) != 0:
                                print("run_port_lldp_pd_req_cmd fails")
                                continue
                            else:
                                pass

                        # 2. call poetool to cofirm alloacted power
                        if (lldp_power_sm[intf] == 1):
                            lldp_power_sm[intf] = 2
                            pass
                        else:
                            result, output_str = run_port_port_status_cmd(
                                poe_dict[intf]["lanes"])
                            # print(result)
                            # print(output_str)
                            if (result == None):
                                print(port_name)
                                result, output_str = run_port_port_status_cmd(
                                    poe_dict[intf]["lanes"])
                                print("try again: ")
                                print(result)
                                continue

                            keyword = "pseAllocatedPwrSingleOrAltA"
                            allocated_power = parse_keyword(
                                keyword, output_str)
                            # print(allocated_power)
                            keyword = "layer2Execution"
                            layer2Execution = parse_keyword(
                                keyword, output_str)
                            # print(layer2Execution)
                            keyword = "layer2Usage"
                            layer2Usage = parse_keyword(keyword, output_str)
                            # print(layer2Usage)

                            # print(single)

                            if (layer2Usage == '1' or layer2Usage == '2' or layer2Usage == '3' or layer2Usage == '4'):
                                # print("xxx")
                                # 3. update allocated power to lldpd
                                port_pri = poe_dict[intf]["priority"]
                                run_lldpcli_config_port_command(
                                    intf, port_pri, int(single) * 100, int(allocated_power) * 100)
                                # 4. Save allocated value to the global variable
                                allocated_values[intf] = allocated_power
                                lldp_power_sm[intf] = 0
                                pass
                            else:
                                # print("yyy")
                                pass

    except (KeyError, json.JSONDecodeError) as e:
        print("Error parsing lldpcli output:", e)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        lldp_debug = sys.argv[1]
    else:
        lldp_debug = "0"

    poe_cfg()

    while True:
        # log.log_error("while True")
        lldpcli_output = run_lldpcli_neighbor_command()
        if lldpcli_output:
            # log.log_error("parse_lldpcli_output")
            parse_lldpcli_output(lldpcli_output)
            if (lldp_debug != "0"):
                # print(port_power_data)
                print(allocated_values)
                print("=============")
        time.sleep(2)
        # log.log_error("time sleep")
