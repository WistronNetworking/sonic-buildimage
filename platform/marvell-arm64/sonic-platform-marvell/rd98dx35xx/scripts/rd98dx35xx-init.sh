#!/bin/bash

# Platform init script
rd98DX35xx_profile()
{
	MAC_ADDR=$(fw_printenv -n ethaddr)
	find /usr/share/sonic/device/*rd98DX35xx* -name profile.ini | xargs sed -i "s/switchMacAddress=.*/switchMacAddress=$MAC_ADDR/g"
	echo "rd98DX35xx: Updating switch mac address ${MAC_ADDR}"
}

# Main
rd98DX35xx_profile

# LOGIC to enumerate SFP eeprom devices - send 0x50 to kernel i2c driver - initialize devices
# the mux may be enumerated at number 4 or 5 so we check for the mux and skip if needed
# Get list of the mux channels
# Enumerate the SFP eeprom device on each mux channel
echo optoe2 0x50 > /sys/bus/i2c/devices/i2c-0/new_device
# Enumerate system eeprom
echo 24c64 0x52 > /sys/class/i2c-adapter/i2c-0/new_device
sleep 2
chmod 644 /sys/class/i2c-adapter/i2c-0/0-0052/eeprom

exit 0
