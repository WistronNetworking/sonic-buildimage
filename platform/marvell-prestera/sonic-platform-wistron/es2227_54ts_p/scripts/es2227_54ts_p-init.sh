#!/bin/bash

# Platform init script

# Load required kernel-mode drivers
load_kernel_drivers() {
    # Remove modules loaded during Linux init
    # FIX-ME: This will be removed in the future when Linux init no longer loads these
    rmmod i2c_dev
    rmmod i2c_mv64xxx

    # Carefully control the load order here to ensure consistent i2c bus numbering
    modprobe i2c_mv64xxx
    modprobe i2c_dev
    insmod /usr/lib/modules/$(uname -r)/kernel/extra/wistron_cpld.ko
    insmod /usr/lib/modules/$(uname -r)/kernel/extra/wistron_max31790.ko
    insmod /usr/lib/modules/$(uname -r)/kernel/extra/wistron_eeprom.ko
    insmod /usr/lib/modules/$(uname -r)/kernel/extra/mvcpss.ko
    modprobe optoe
    modprobe jc42
}

# - Main entry

# Install kernel drivers required for i2c bus access
load_kernel_drivers
#entropy setting
#python /etc/entropy.py
gpio_chip_base() {
    local label="$1"
    local chip
    for chip in /sys/class/gpio/gpiochip*; do
        [ -f "$chip/label" ] || continue
        if [ "$(cat "$chip/label")" = "$label" ]; then
            cat "$chip/base"
            return 0
        fi
    done
    return 1
}

    echo wistron_cpld 0x33 > /sys/bus/i2c/devices/i2c-0/new_device
    echo 2227_max31790 0x2f > /sys/bus/i2c/devices/i2c-2/new_device
    echo wistron_eeprom 0x50 > /sys/bus/i2c/devices/i2c-3/new_device
    echo wistron_eeprom 0x51 > /sys/bus/i2c/devices/i2c-3/new_device

    echo jc42 0x1b > /sys/bus/i2c/devices/i2c-0/new_device
    echo pmbus 0x58 > /sys/bus/i2c/devices/i2c-3/new_device
    echo pmbus 0x59 > /sys/bus/i2c/devices/i2c-3/new_device

    # PCA9555 @ mux ch0 (i2c-2) addr 0x20. Not in shared DTS (p2 has no chip);
    # instantiate from userspace. Kernel 6.12 gpiochip base is dynamic.
    if i2cget -f -y 2 0x20 0 >/dev/null 2>&1; then
        echo pca9555 0x20 > /sys/bus/i2c/devices/i2c-2/new_device
    else
        echo "WARNING: pca9555 @2-0020 not found; skip" >&2
    fi

    tca_detect=$(i2cget -f -y 5 0x22 0x40 1>/dev/null 2>/dev/null; echo $?)
    if [ $tca_detect -eq 0 ]; then
		i2cset -y 5 0x22 0x54 0x22
		i2cset -y 5 0x22 0x55 0x22
		i2cset -y 5 0x22 0x56 0x22
        echo pcal6524 0x22 > /sys/bus/i2c/devices/i2c-5/new_device
    else
        echo tca6424 0x22 > /sys/bus/i2c/devices/i2c-5/new_device
    fi

    local i
    for i in {4..9};
    do
        echo optoe2 0x50 > /sys/bus/i2c/devices/i2c-$i/new_device
    done

    # TCA6424/PCAL6524 @5-0022: 24 lines. Absolute numbers used to be 472-495.
    sfp_base=$(gpio_chip_base "5-0022") || sfp_base=""
    if [ -n "$sfp_base" ]; then
        for j in $(seq "$sfp_base" $((sfp_base + 23))); do
            echo $j > /sys/class/gpio/export 2>/dev/null
        done
        # tx-disable pins (base+1, +5, ...) as output
        for k in $(seq $((sfp_base + 1)) 4 $((sfp_base + 21))); do
            echo out > /sys/class/gpio/gpio$k/direction 2>/dev/null
        done
    else
        echo "WARNING: gpiochip label 5-0022 not found; skip SFP GPIO export" >&2
    fi

    # PCA9555 @2-0020: only offsets 0/2/3 (were gpio 496/498/499) for oob-led
    pca_base=$(gpio_chip_base "2-0020") || pca_base=""
    if [ -n "$pca_base" ]; then
        for off in 0 2 3; do
            echo $((pca_base + off)) > /sys/class/gpio/export 2>/dev/null
            echo out > /sys/class/gpio/gpio$((pca_base + off))/direction 2>/dev/null
        done
    fi

    for i in {0..2};
    do
        echo 85000 > /sys/class/hwmon/hwmon$i/temp1_max
        echo 80000 > /sys/class/hwmon/hwmon$i/temp1_max_hyst
    done

	echo 80000 > /sys/class/hwmon/hwmon4/temp1_max
	echo 85000 > /sys/class/hwmon/hwmon4/temp1_crit

    echo 1 > /sys/bus/i2c/devices/0-0033/port_led_auto

    sh /usr/local/bin/poe_init.sh

exit 0
