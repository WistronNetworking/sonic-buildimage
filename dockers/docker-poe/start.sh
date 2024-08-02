#!/usr/bin/env bash

rm -f /var/run/rsyslogd.pid
rm -f /var/run/stpd/*
rm -f /var/run/stpmgrd/*

python3 -c "import sonic_platform" > /dev/null 2>&1 || pip3 show sonic-platform > /dev/null 2>&1
if [ $? -ne 0 ]; then
    SONIC_PLATFORM_WHEEL="/usr/share/sonic/platform/sonic_platform-1.0-py3-none-any.whl"
    echo "sonic-platform package not installed, attempting to install..."
    if [ -e ${SONIC_PLATFORM_WHEEL} ]; then
       pip3 install ${SONIC_PLATFORM_WHEEL}
       if [ $? -eq 0 ]; then
          echo "Successfully installed ${SONIC_PLATFORM_WHEEL}"
          SONIC_PLATFORM_API_PYTHON_VERSION=3
       else
          echo "Error: Failed to install ${SONIC_PLATFORM_WHEEL}"
       fi
    else
       echo "Error: Unable to locate ${SONIC_PLATFORM_WHEEL}"
    fi
else
    SONIC_PLATFORM_API_PYTHON_VERSION=3
fi

supervisorctl start rsyslogd

supervisorctl start poeinit

supervisorctl start poeallocp

