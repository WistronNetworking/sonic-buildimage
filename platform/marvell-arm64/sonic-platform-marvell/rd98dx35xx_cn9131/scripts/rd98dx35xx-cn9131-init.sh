#!/bin/bash

# Platform init script for rd98dx35xx-cn9131

rd98dx35xx-cn9131_profile()
{
    MAC_ADDR=$(fw_printenv -n ethaddr)
    find /usr/share/sonic/device/*rd98DX35xx_cn9131* -name profile.ini | xargs sed -i "s/switchMacAddress=.*/switchMacAddress=$MAC_ADDR/g"
    echo "rd98dx35xx_cn9131: Updating switch mac address ${MAC_ADDR}"
}

# - Main entry
rd98dx35xx-cn9131_profile

exit 0
