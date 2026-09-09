#!/bin/bash

COPPER_TYPE=0
FIBER_TYPE=1

currMediaType=0

RET=-1

# PCA9555 @ i2c-2 0x20 (label 2-0020). Offsets 0/2/3 were gpio 496/498/499.
PCA_BASE=""
GPIO_TXDIS=""
GPIO_LED_A=""
GPIO_LED_B=""

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

set_led()
{
    if [ -z "$PCA_BASE" ]; then
        echo "WARNING: pca9555 gpiochip 2-0020 not found; skip LED" >&2
        return 0
    fi

    if [ $1 -eq $COPPER_TYPE ]; then
        a=0; b=1
    else
        a=1; b=0
    fi

    echo $a > /sys/class/gpio/gpio$GPIO_LED_A/value; RET=`echo $?`
    if [ $RET -ne 0 ]; then
        echo "WARNING: pca9555 combo LED gpio write failed" >&2
        return 0
    fi

    echo $b > /sys/class/gpio/gpio$GPIO_LED_B/value; RET=`echo $?`
    if [ $RET -ne 0 ]; then
        echo "WARNING: pca9555 combo LED gpio write failed" >&2
        return 0
    fi
}

init()
{
    # init oob port led
    phytool write eth0/0/22 3;
    phytool write eth0/0/17 0x44a5;
    phytool write eth0/0/22 3;
    phytool write eth0/0/16 0x0240;
    phytool write eth0/0/22 0

    # set rj port sgmii amplitude
    phytool write eth0/0/22 2;
    phytool write eth0/0/26 0x8004;
    phytool write eth0/0/22 0

    PCA_BASE=$(gpio_chip_base "2-0020") || PCA_BASE=""
    if [ -z "$PCA_BASE" ]; then
        echo "WARNING: pca9555 gpiochip 2-0020 not found; skip combo GPIO" >&2
    else
        GPIO_TXDIS=$((PCA_BASE + 0))
        GPIO_LED_A=$((PCA_BASE + 2))
        GPIO_LED_B=$((PCA_BASE + 3))
        for g in "$GPIO_TXDIS" "$GPIO_LED_A" "$GPIO_LED_B"; do
            echo $g > /sys/class/gpio/export 2>/dev/null
            echo out > /sys/class/gpio/gpio$g/direction 2>/dev/null
        done

        # default set sfp txdisable to off
        echo 0 > /sys/class/gpio/gpio$GPIO_TXDIS/value

        # default set to copper port mode
        currMediaType=$COPPER_TYPE
        set_led $COPPER_TYPE
    fi

    # Disable fiber Auto-Negotiation
    phytool write eth0/0/22 1; RET=`echo $?`
    if [ $RET -ne 0 ]; then exit 3; fi
    ethtool -s eth0 autoneg off speed 1000 duplex full; RET=`echo $?`
    if [ $RET -ne 0 ]; then exit 4; fi
    #echo "Disable fiber Auto-Negotiation"

    # Set auto select mode - Prefer fiber medium
    phytool write eth0/0/22 2; RET=`echo $?`
    if [ $RET -ne 0 ]; then exit 5; fi
    phytool write eth0/0/16 0x508; RET=`echo $?`
    if [ $RET -ne 0 ]; then exit 6; fi
    #echo "Set auto select mode - Prefer fiber medium"

    # Enable auto medium register selection
    phytool write eth0/0/22 0x8000; RET=`echo $?`
    if [ $RET -ne 0 ]; then exit 7; fi
    #echo "Enable auto medium register selection"
}

do_task()
{
    while true; do
        regVal=`phytool read eth0/0/22`; RET=`echo $?`
        if [ $RET -ne 0 ]; then exit 8; fi

        type=$((regVal & 0x1))

        if [ $type -ne $currMediaType ]; then
            if [ $type -eq $COPPER_TYPE ]; then
                set_led $COPPER_TYPE
                #echo "set led to copper mode"
            else
                set_led $FIBER_TYPE
                #echo "set led to fiber mode"
            fi

            currMediaType=$type
        fi

        sleep 2
    done
}

main()
{
    init
    do_task
}

main
