
/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/


#ifndef _WISTRON_SWITCH_CPLD_H
#define _WISTRON_SWITCH_CPLD_H

//#define CPLD_REVISION_REG    	    0x0
#define CPLD_HW_REVISION_REG        0x1
#define CPLD_MJR_REVISION_REG       0x2
#define CPLD_MNR_REVISION_REG       0x3
#define CPLD_DBG_REVISION_REG       0x4
#define CPLD_BUILD_YEAR_REG         0x5
#define CPLD_BUILD_MON_REG          0x6
#define CPLD_BUILD_DATE_REG         0x7
#define CPLD_RW_TEST_REG            0x8

#define CPLD_POWER_GOOD_REG         0x9
#define CPLD_POWER_GOOD2_REG        0xA

#define CPLD_FAN_CTL1_REG           0xB
#define CPLD_FAN_CTL2_REG           0xC


#define CPLD_1G_LED_CTL1_REG        0xD
#define CPLD_1G_LED_CTL2_REG        0xE
#define CPLD_1G_LED_CTL3_REG        0xF
#define CPLD_1G_LED_CTL4_REG        0x10
#define CPLD_1G_LED_CTL5_REG        0x11
#define CPLD_1G_LED_CTL6_REG        0x12
#define CPLD_1G_LED_CTL7_REG        0x13
#define CPLD_1G_LED_CTL8_REG        0x14
#define CPLD_1G_LED_CTL9_REG        0x15
#define CPLD_1G_LED_CTL10_REG       0x16
#define CPLD_1G_LED_CTL11_REG       0x17
#define CPLD_1G_LED_CTL12_REG       0x18
#define CPLD_1G_LED_CTL13_REG       0x19
#define CPLD_1G_LED_CTL14_REG       0x1A
#define CPLD_1G_LED_CTL15_REG       0x1B
#define CPLD_1G_LED_CTL16_REG       0x1C

#define CPLD_POE_PRT_LED_CTL1_REG   0x25
#define CPLD_POE_PRT_LED_CTL2_REG   0x26
#define CPLD_POE_PRT_LED_CTL3_REG   0x27
#define CPLD_POE_PRT_LED_CTL4_REG   0x28
#define CPLD_POE_PRT_LED_CTL5_REG   0x29
#define CPLD_POE_PRT_LED_CTL6_REG   0x2A

#define CPLD_SFP_LED_CTL1_REG       0x31
#define CPLD_SFP_LED_CTL2_REG       0x32

#define CPLD_SYS_LED_CTL_REG        0x35

#define CPLD_RESET_CTL1_REG         0x36
#define CPLD_RESET_CTL2_REG         0x37

#define CPLD_INT_CTL1_REG           0x38
#define CPLD_INT_CTL2_REG           0x39
#define CPLD_INT_CTL3_REG           0x3A
#define CPLD_MASK1_REG              0x3B
#define CPLD_MSCI_CTL_REG           0x3C
#define CPLD_PORT_LED_MANUAL_REG    0x3D //Test Mode Register
#define CPLD_RESET_CTL3_REG         0x3E
#define CPLD_MSCI_CTL2_REG          0x3F
#define CPLD_WATCHDOG_REG           0x40
#define CPLD_SYS_POWER_DOWN_REG     0x41

#define CPLD_SFP_PROT1_CTRL_REG     0x50
#define CPLD_SFP_PROT2_CTRL_REG     0x51
#define CPLD_SFP_PROT3_CTRL_REG     0x52
#define CPLD_SFP_PROT4_CTRL_REG     0x53

static char* power_ctrl_str1[] = {
    "P1V2_OOB_EN", // 0
    "P1V5_EN",
    "P1V8_CPU_EN",
    "P1V8",
    "P2V5_CPU_DDR4_EN",
    "P2V5_EN",
    "P3V3_EN",
    "P5V0_EN",   // 7
};

static char* power_ctrl_str2[] = {
    "PHY_P0V8_EN", // 0
    "P0V8_MAC_EN",
    "P0V85_CPU_AP_VDD_EN",
    "P1V15_EN",
    "P0V6_CPU_DDR4_VTT_EN",
    "P0V6_EN",
    "P1V2_CPU_DDR4_EN",
    "P1V2_EN",  // 7
};

static char* power_good_str1[] = {
    "RESERVED",            // 0
    "PWRGD_P1V5",
    "PWRGD_P1V8_CPU",
    "PWRGD_P1V8",
    "PWRGD_P2V5_CPU_DDR4",
    "PWRGD_P54V_N",
    "PWRGD_P3V3",
    "PWRGD_P5V0",          // 7
};

static char* power_good_str2[] = {
    "RESERVED",            // 0
    "PWRGD_VR_MAC_P0V8",
    "PWRGD_P0V85_CPU_AP_VDD",
    "PWRGD_P1V15",
    "PWRGD_VR_P0V6_CPU_DDR4_VTT",
    "PWRGD_P1V8_CLK_BUF",
    "PWRGD_P1V2_CPU_DDR4",
    "PWRGD_P1V2",          // 7
};

enum psu_status_bit {
    PSU2_ON = 0,
    PSU1_ON,
    PSU2_AC_OK,
    PSU1_AC_OK,
    PSU2_PRESENT,
    PSU1_PRESENT,
    PSU2_PWRGD,
    PSU1_PWRGD,
};



enum port_id {
    PORT1 = 0,
    PORT2,
    PORT3,
    PORT4,
    PORT5,
    PORT6,
    PORT7,
    PORT8,
    PORT9,
    PORT10,
    PORT11,
    PORT12,
    PORT13,
    PORT14,
    PORT15,
    PORT16,
    PORT17,
    PORT18,
    PORT19,
    PORT20,
    PORT21,
    PORT22,
    PORT23,
    PORT24,
    PORT25,
    PORT26,
    PORT27,
    PORT28,
    PORT29,
    PORT30,
    PORT31,
    PORT32,
    PORT33,
    PORT34,
    PORT35,
    PORT36,
};

enum led_status_shift_bit {
    LOC_LED = 0,
    ALM2_LED = 2,
    SYS_LED = 4,
    ALM1_LED = 6,
};

enum rst_shift_bit {
    RST_MB_IOIEXP = 0,
    RST_PHY0_5 = 1,
    RST_POE_CTR = 2,
    CPLD_SYSRST_MAC_RST = 3,
    RST_CPU_SYSRST_OUT = 4,
    RST_GPHY_RST = 5,
    RST_I2C_MUX = 6,
    RST_MAC_JTAG = 7,
    RST_BUTTON_CPLD_CPU = 8,
    RST_BUTTON = 9,
    RST_CPLD_I2C_MUX = 10,
    RST_BUTTON_10S = 1,
};

enum int_shift_bit {
    INT_PHY0 = 0,
    INT_PHY1 = 1,
    INT_PHY2 = 2,
    INT_PHY3 = 3,
    INT_PHY4_0 = 4,
    INT_PHY4_1 = 5,
    INT_PHY5_0 = 6,
    INT_PHY5_1 = 7,

    INT_OOB_IOIEXP = 8,
    INT_MB_IOIEXP = 9,
    USB_OCP_ALERT = 10,
    TEMP_ALERT = 11,
    RTC_IRQ = 12,
    PSU2_INT_ALERT = 13,
    PSU1_INT_ALERT = 14,
    INT_PD69210 = 15,

    DEV_INIT_DONE = 16,
    FAN_CTL_FAIL = 17,
    MEMHOT = 18,

    MB_SYSTEM_INT = 19,
    MB_MOD_PRE_INT = 20,
};

enum msci_ctl2_shift_bit {
    SKU1_PSU1 = 0,
    SKU2_PSU2 = 1,
};

enum sysled_en_shift_bit {
    FRONT_PORT_LED_EN = 0,
    FRONT_SFP_LED_EN = 5,
};

enum watchdog_timer_def {
    WATCHDOG_180S = 0,
    WATCHDOG_210S = 1,
    WATCHDOG_240S = 2,
    WATCHDOG_270S = 3,
    WATCHDOG_300S = 4,
};

enum sfp_port_ctrl {
    PORT1_TX_DISABLE = 0,
    PORT1_RATE_SEL0 = 1,
    PORT1_RATE_SEL1 = 2,
    PORT1_LOSS = 3,
    PORT1_PRESENT = 4,
    PORT1_FAULT = 5,
    PORT2_TX_DISABLE = 6,
    PORT2_RATE_SEL0 = 7,
    PORT2_RATE_SEL1 = 8,
    PORT2_LOSS = 9,
    PORT2_PRESENT = 10,
    PORT2_FAULT = 11,
    PORT3_TX_DISABLE = 12,
    PORT3_RATE_SEL0 = 13,
    PORT3_RATE_SEL1 = 14,
    PORT3_LOSS = 15,
    PORT3_PRESENT = 16,
    PORT3_FAULT = 17,
    PORT4_TX_DISABLE = 18,
    PORT4_RATE_SEL0 = 19,
    PORT4_RATE_SEL1 = 20,
    PORT4_LOSS = 21,
    PORT4_PRESENT = 22,
    PORT4_FAULT = 23,
};

#endif /* _WISTRON_SWITCH_CPLD_H */
