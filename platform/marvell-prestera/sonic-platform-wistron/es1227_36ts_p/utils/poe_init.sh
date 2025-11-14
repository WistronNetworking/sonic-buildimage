#!/bin/sh

echo 1 > /sys/bus/i2c/devices/0-0033/poe_en_ctrl

# Set temporary matrix
#poe_main 0x11 0 4 255
#poe_main 0x11 1 5 255
#poe_main 0x11 2 6 255
#poe_main 0x11 3 7 255
#poe_main 0x11 4 0 255
#poe_main 0x11 5 1 255
#poe_main 0x11 6 2 255
#poe_main 0x11 7 3 255
#poe_main 0x11 8 12 255
#poe_main 0x11 9 13 255
#poe_main 0x11 10 14 255
#poe_main 0x11 11 15 255
#poe_main 0x11 12 8 255
#poe_main 0x11 13 9 255
#poe_main 0x11 14 10 255
#poe_main 0x11 15 11 255
#poe_main 0x11 16 19 255
#poe_main 0x11 17 18 255
#poe_main 0x11 18 17 255
#poe_main 0x11 19 16 255
#poe_main 0x11 20 23 255
#poe_main 0x11 21 22 255
#poe_main 0x11 22 21 255
#poe_main 0x11 23 20 255

# Set 2 pair AT compliant modes
#for port in $(seq 0 23);do
#    poe_main 0x02 $port 1 1 9 3
#done

# Set 4 pair BT compliant modes
#for port in $(seq 32 47);do
#    poe_main 0x02 $port 1 1 0 3
#done

# PSUx1 = {power bank 0x01}
# PSUx2 = {power bank 0x10}
# For PSU1(250W AC adapter)
#poe_main 0x09 1 250 585 480
poetool mgmt set_power_banks 1  250 585 480 0xa
# For PSU2(400W AC adapter)
#poe_main 0x09 2 400 585 480
poetool mgmt set_power_banks 2 400 585 480 0xa

# Program global matrix
#poe_main 0x13
# Save system settings
#poe_main 0x15

# PoE power bank setting
#poe_power.sh
# reload PoE configuration
poe_cfg_init.py

