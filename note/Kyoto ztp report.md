# VLAN 951 DHCP Relay Test Report — Wistron_M.1.0.2

## 1. Test Objective

Verify that a DUT running `Wistron_M.1.0.2` on console port 6 can obtain an IPv4 address on `Vlan951` through a SONiC DHCP relay and an ISC DHCP server.

This test verifies the normal relay path without the relay-side `ebtables` unicast rewrite workaround.

The key mechanism under test is `SAI_HOSTIF_TRAP_TYPE_DHCP_L2` (`dhcp_l2`), added to the SONiC COPP pipeline by `0002-Add-dhcp_l2-dhcpv6_l2-to-copp-supported-list.patch`, which punts L2 broadcast DHCP frames to the CPU.

---

## 2. Test Topology

| Role | Management IP | Interface / Port | VLAN / IP | Function |
| :--- | :--- | :--- | :--- | :--- |
| DHCP server | `192.168.80.180` | `Ethernet9` | `Vlan2`, `10.90.90.205/24` | ISC DHCP server |
| DHCP relay | `192.168.80.179` | `Ethernet1` | `Vlan2`, `10.90.90.11/24` | Uplink to DHCP server |
| DHCP relay | `192.168.80.179` | `Ethernet3` | `Vlan951`, `192.168.10.1/24` | Downlink to DUT |
| DUT / port 6 | `192.168.80.174` | `Ethernet11` | `Vlan951`, DHCP client | ZTP DHCP client |

Physical cabling:
```text
DHCP server Ethernet9 <-> relay Ethernet1
relay Ethernet3       <-> DUT Ethernet11 / console server port 6
```

DHCP pool:
```text
192.168.10.100 - 192.168.10.109
```

Software versions:
```text
DUT:    SONiC.Wistron_M.1.0.2   Build commit: 05730ef5f  Build date: Fri Jun 12 06:25:20 UTC 2026
Server: SONiC.Wistron.3.1.0    HwSku: wistron_es2227_54ts_p
Relay:  SONiC.Wistron.3.1.0    HwSku: wistron_es1227_54ts
```

DUT COPP `queue4_group3` trap_ids (AppDB):
```text
dhcp,dhcp_l2,dhcpv6,dhcpv6_l2,lldp,udld
```

---

## 3. DHCP Server Setup and Verification

### 3.1 Fresh Image / After Reflash Setup (One-time Setup)

> [!NOTE]
> These steps configure the persistent environment for the DHCP server.

```text
# 0. Disable ZTP on the SERVER (the server is not a ZTP client).
#    Leaving ZTP running puts ports in routed discovery mode and fights the VLAN config.
sudo config ztp disable -y          # triggers a config reload; wait until it settles
#    -> verify: show ztp status  ->  "ZTP Admin Mode : False"

#    After ztp disable, the default config has Ethernet9 as a routed port with a
#    default IP (e.g. 10.0.0.16/31). Remove it before adding Ethernet9 to Vlan2:
sudo config interface ip remove Ethernet9 10.0.0.16/31   # use the IP shown by: show ip interfaces | grep Ethernet9

# 1. SONiC VLAN setup (saved to config_db.json, survives reboot)
sudo config vlan add 2
sudo config vlan member add 2 -u Ethernet9
sudo config interface ip add Vlan2 10.90.90.205/24
sudo config save -y

# 2. Install ISC DHCP server from local debs.
#    The correct package set depends on the server image's Debian base:
#      check with:  cat /etc/os-release | grep VERSION  ;  dpkg -l libc6 | tail -1

#    --- Variant A: SONiC.Wistron_M.1.0.2  (Debian 12 / bookworm, libc6 >= 2.36) ---
#        Use isc_dhcp/provided/  (isc-dhcp 4.4.3-P1-2). Do NOT use isc_dhcp/debs/.
scp isc_dhcp/provided/isc-dhcp-common_arm64.deb \
    isc_dhcp/provided/isc-dhcp-server_arm64.deb \
    admin@<server>:/tmp/
ssh admin@<server> \
  'sudo dpkg -i /tmp/isc-dhcp-common_arm64.deb /tmp/isc-dhcp-server_arm64.deb'

#    --- Variant B: SONiC.Wistron.3.1.0  (Debian 11 / bullseye, libc6 2.31) ---
#        Use isc_dhcp/debs/  (isc-dhcp 4.4.1-2.3+deb11u2, bundles all bullseye deps).
#        The provided/ 4.4.3 packages FAIL here: "depends on libc6 (>= 2.36)".
scp isc_dhcp/debs/*.deb isc_dhcp/scripts/init-functions.lsb admin@<server>:/tmp/
ssh admin@<server> '
  sudo dpkg --remove isc-dhcp-server isc-dhcp-common 2>/dev/null   # clean any failed 4.4.3 install
  sudo fuser -k 67/udp 2>/dev/null
  sudo DEBIAN_FRONTEND=noninteractive dpkg --force-confdef --force-confold -i \
     /tmp/libisc-export1105_9.11.19+dfsg-2.1_arm64.deb \
     /tmp/libdns-export1110_9.11.19+dfsg-2.1_arm64.deb \
     /tmp/libisccfg-export163_9.11.19+dfsg-2.1_arm64.deb \
     /tmp/libirs-export161_9.11.19+dfsg-2.1_arm64.deb \
     /tmp/lsb-base_11.6_all.deb \
     /tmp/isc-dhcp-common_4.4.1-2.3+deb11u2_arm64.deb \
     /tmp/isc-dhcp-server_4.4.1-2.3+deb11u2_arm64.deb
  # REQUIRED FIX: lsb-base_11.6_all.deb is a DUMMY (no files); it overwrites the real
  # lsb-base and removes /lib/lsb/init-functions, so the init script fails with:
  #   "/etc/init.d/isc-dhcp-server: .: cannot open /lib/lsb/init-functions: No such file"
  # Restore it from the bundled minimal copy:
  sudo mkdir -p /lib/lsb
  sudo cp /tmp/init-functions.lsb /lib/lsb/init-functions
  sudo chmod 644 /lib/lsb/init-functions
'

# 3. DHCP server config
sudo tee /etc/dhcp/dhcpd.conf << 'EOF'
authoritative;
default-lease-time 1200;
max-lease-time 1200;
log-facility local7;

subnet 10.90.90.0 netmask 255.255.255.0 {
}

subnet 192.168.10.0 netmask 255.255.255.0 {
  range 192.168.10.100 192.168.10.109;
  option routers 192.168.10.1;
  option subnet-mask 255.255.255.0;
  option domain-name "test.ntc.lab";
  option domain-name-servers 8.8.8.8;
}
EOF

# 4. Tell isc-dhcp-server to listen on Vlan2
sudo sed -i 's/INTERFACESv4=""/INTERFACESv4="Vlan2"/' /etc/default/isc-dhcp-server
```

### 3.2 Post-reboot Verification (After Every Server Reboot)

> [!WARNING]
> Static routes and daemon status are not persisted after reboots. Run these commands to restore connectivity.

```text
# 1. If Vlan2 is LOWERLAYERDOWN / route shows "linkdown" / ping to relay fails even
#    though "show interfaces status Ethernet9" says oper up, the Ethernet9 kernel
#    netdev did not sync into the bridge. Bounce the port to fix it:
#    (check first:  ip -br addr show Vlan2   -> must NOT say LOWERLAYERDOWN
#                   cat /sys/class/net/Vlan2/carrier   -> must be 1 )
sudo config interface shutdown Ethernet9
sudo config interface startup Ethernet9

# 2. Restore static route and DHCP daemon.
#    Use replace instead of add so the command is idempotent.
sudo ip route replace 192.168.10.0/24 via 10.90.90.11 dev Vlan2
sudo systemctl restart isc-dhcp-server

# 3. Verify the path to the relay is up before testing the client.
#    If isc-dhcp-server is failed, check:
#      journalctl -u isc-dhcp-server --no-pager -n 80
#    Known failure after reboot:
#      No subnet declaration for Vlan2 (no IPv4 addresses)
#    That means dhcpd started before Vlan2 got 10.90.90.205/24; restart it after Vlan2 is up.
systemctl is-active isc-dhcp-server   # must be active
ping -I Vlan2 -c 2 10.90.90.11      # must be 0% loss
```

### 3.3 Server Status Checks

```

$ `show vlan brief`

| VLAN ID | IP Address | Ports | Port Tagging | Proxy ARP | DHCP Helper Address |
| :---: | :--- | :--- | :--- | :---: | :--- |
| 2 | `10.90.90.205/24` | `Ethernet9` | untagged | disabled | |

其他介面與路由檢查：
```text
$ ip -br addr | grep -E 'eth0|Ethernet9|Vlan2'
eth0             UP             192.168.80.180/24 fe80::251:82ff:fe11:2201/64
Ethernet9        UP             fe80::211:22ff:fe33:4401/64
Vlan2@Bridge     UP             10.90.90.205/24 fe80::211:22ff:fe33:4401/64

$ ip route show | grep -E '10.90.90|192.168.10|default'
default via 192.168.80.254 dev eth0
10.90.90.0/24 dev Vlan2 proto kernel scope link src 10.90.90.205
192.168.10.0/24 via 10.90.90.11 dev Vlan2

$ ss -ulpn | grep ':67'
UNCONN 0 0 0.0.0.0:67 0.0.0.0:* users:(("dhcpd",pid=10060,fd=8))

$ ps -ef | grep '[d]hcpd'
root 10060 1 0 19:52 ? 00:00:00 /usr/sbin/dhcpd -4 -q -cf /etc/dhcp/dhcpd.conf Vlan2
```

---

## 4. DHCP Relay Setup and Verification

```text
# 0. Disable ZTP on the RELAY (the relay is not a ZTP client).
#    Leaving ZTP running puts ports in routed discovery mode and fights the VLAN config.
$ sudo config ztp disable -y          # triggers a config reload; wait until it settles
#    -> verify: show ztp status  ->  "ZTP Admin Mode : False"

$ show feature status | grep dhcp
dhcp_relay      enabled          enabled         local

$ systemctl is-active dhcp_relay
active
```

$ `show vlan brief`

| VLAN ID | IP Address | Ports | Port Tagging | Proxy ARP | DHCP Helper Address |
| :---: | :--- | :--- | :--- | :---: | :--- |
| 2 | `10.90.90.11/24` | `Ethernet1` | untagged | disabled | |
| 951 | `192.168.10.1/24` | `Ethernet3` | untagged | disabled | `10.90.90.205` |

進程與 ebtables 狀態檢查：
```text

$ docker exec dhcp_relay supervisorctl status
dhcp-relay:isc-dhcpv4-relay-Vlan951   RUNNING
dhcpmon:dhcpmon-Vlan951               RUNNING

$ ps -ef | grep '[d]hcrelay'
root ... /usr/sbin/dhcrelay ... -id Vlan951 -iu Vlan2 ... 10.90.90.205

$ ebtables -t nat -L OUTPUT --Lc
Bridge table: nat
Bridge chain: OUTPUT, entries: 0, policy: ACCEPT
```

Relay LLDP topology check:
```text
$ show lldp neighbors | grep -A16 -E 'Interface: +Ethernet1|Interface: +Ethernet3'
Interface:    Ethernet1
  ChassisID:    mac 00:51:82:11:22:01
  SysDescr:     SONiC.Wistron.3.1.0 - HwSku: wistron_es2227_54ts_p
  MgmtIP:       192.168.80.180
  PortID:       local Eth9
  PortDescr:    Ethernet9

Interface:    Ethernet3
  ChassisID:    mac 5c:ff:35:e9:55:51
  SysDescr:     SONiC.Wistron_M.1.0.2 - HwSku: wistron_es1227_54ts_p2
  MgmtIP:       192.168.80.174
  PortID:       local Eth11
  PortDescr:    Ethernet11
```

> [!NOTE]
> DUT MgmtIP shows `240.127.1.1` (internal bridge IP) because the management IP is obtained via ZTP and was not yet assigned when LLDP was captured.

Relay-to-server connectivity:
```text
$ ping -I Vlan2 -c 3 -W 1 10.90.90.205
3 packets transmitted, 3 received, 0% packet loss
```

---

## 5. DUT Port 6 Reflash SOP

Use this SOP after reflashing the DUT. The DUT is still reached through console server port 6 and management IP `192.168.80.174`.

Before touching the DUT, verify the server and relay path:
```text
# DHCP server, 192.168.80.180
$ systemctl is-active isc-dhcp-server
active

$ ip route get 192.168.10.1
192.168.10.1 via 10.90.90.11 dev Vlan2 src 10.90.90.205

# DHCP relay, 192.168.80.179
$ docker exec dhcp_relay supervisorctl status | grep Vlan951
dhcp-relay:isc-dhcpv4-relay-Vlan951   RUNNING

$ ping -I Vlan2 -c 3 -W 1 10.90.90.205
3 packets transmitted, 3 received, 0% packet loss
```

### 5.1 Configure Vlan951 and Trigger ZTP Hook

On a fresh DUT image, ZTP may first grab `Ethernet11` as a routed DHCP port. Remove any IPv4 address from `Ethernet11`, create `Vlan951`, and restart ZTP to run the AMAZON VLAN hook.

Use `systemctl restart ztp`, not `config ztp run`. `config ztp run` performs a config reload and can erase the runtime `Vlan951` setup.

```text
$ show ver
SONiC Software Version: SONiC.Wistron_M.1.0.2
Distribution: Debian 12.13
Kernel: 6.1.0-29-2-arm64
HwSKU: wistron_es1227_54ts_p2

$ ip -br addr | grep -E 'eth0|Ethernet11|Vlan951'
eth0             UP             192.168.80.174/24
Ethernet11       UP             192.168.10.x/24

$ for ip in $(ip -o -4 addr show dev Ethernet11 | awk '{print $4}'); do config interface ip remove Ethernet11 "$ip"; done

$ config vlan add 951

$ config vlan member add 951 -u Ethernet11

$ ip link set Vlan951 up

$ sed -i 's/AMAZON_FLAG=false/AMAZON_FLAG=true/' /usr/lib/ztp/ztp-profile.sh

$ grep -n 'AMAZON_FLAG\|HOOK_ALL_VLANS' /usr/lib/ztp/ztp-profile.sh
41:AMAZON_FLAG=true
43:HOOK_ALL_VLANS=true

$ show vlan brief
|       951 |              | Ethernet11 | untagged       | disabled    |                       |
```

Clean any previous `Vlan951` DHCP process and restart ZTP:
```text
$ for p in $(pgrep -f '^dhclient .*Vlan951|^/sbin/dhclient .*Vlan951'); do kill "$p"; done
$ rm -f /run/dhclient.Vlan951.pid /var/lib/dhcp/dhclient.Vlan951.leases
$ ip addr flush dev Vlan951
$ ip link set Vlan951 up
$ systemctl restart ztp
```

Expected ZTP hook result:
```text
$ journalctl -u ztp --no-pager -n 120 | grep -E 'Create static|CPU port|VLANs from ip link|Adding VLAN member|Hook DHCP|Starting DHCP client|DHCPOFFER|DHCPACK|bound'
Create static MAC etnry for all vlan.
CPU port (fixed): 63
VLANs from ip link: [951]
Adding VLAN member (CPU port 63, tagged)...
Hook DHCP client.
Starting DHCP client on Vlan951
DHCPOFFER of 192.168.10.x from 192.168.10.1
DHCPACK of 192.168.10.x from 192.168.10.1
bound to 192.168.10.x -- renewal in ... seconds.

$ ip -br addr | grep -E 'eth0|Ethernet11|Vlan951'
eth0             UP             192.168.80.174/24
Ethernet11       UP             fe80::...
Vlan951@Bridge   UP             192.168.10.x/24
```

### 5.2 Install Generic VLAN DHCP Route Hook

Install a generic hook. Do not write this as `interface = Vlan951`; the route problem applies to any `Vlan*` DHCP interface.

**Reason:**
* DHCP server identifier: `10.90.90.205`
* **Without this hook**: DHCP renew unicast to `10.90.90.205` follows the default route, usually `eth0` -> renew `ACK` is not received on `Vlan951` -> lease can expire.
* **With this hook**: `dhclient` receives `BOUND`/`RENEW`/`REBIND`/`REBOOT` on `Vlan*` -> add host route to the DHCP server via the DHCP router option -> `10.90.90.205/32 via 192.168.10.1 dev Vlan951`.

Create `/etc/dhcp/dhclient-exit-hooks.d/vlan_dhcp_server_route`:
```sh
#!/bin/sh

case "$reason" in
  BOUND|RENEW|REBIND|REBOOT)
    if echo "$interface" | grep -q '^Vlan' && \
       [ -n "$new_dhcp_server_identifier" ] && \
       [ -n "$new_routers" ]; then
      gw=$(echo "$new_routers" | awk '{print $1}')
      ip route replace "$new_dhcp_server_identifier/32" via "$gw" dev "$interface"
    fi
    ;;
esac
```

Set execution permission and verify:
```text
$ chmod +x /etc/dhcp/dhclient-exit-hooks.d/vlan_dhcp_server_route
$ sh -n /etc/dhcp/dhclient-exit-hooks.d/vlan_dhcp_server_route
$ sed -n '1,80p' /etc/dhcp/dhclient-exit-hooks.d/vlan_dhcp_server_route
#!/bin/sh

case "$reason" in
  BOUND|RENEW|REBIND|REBOOT)
    if echo "$interface" | grep -q '^Vlan' && \
       [ -n "$new_dhcp_server_identifier" ] && \
       [ -n "$new_routers" ]; then
      gw=$(echo "$new_routers" | awk '{print $1}')
      ip route replace "$new_dhcp_server_identifier/32" via "$gw" dev "$interface"
    fi
    ;;
esac
```

Renew once to let the hook run:
```text
$ for p in $(pgrep -f '^dhclient .*Vlan951|^/sbin/dhclient .*Vlan951'); do kill "$p"; done
$ rm -f /run/dhclient.Vlan951.pid
$ ip addr flush dev Vlan951
$ ip link set Vlan951 up
$ dhclient -v -pf /run/dhclient.Vlan951.pid -lf /var/lib/dhcp/dhclient.Vlan951.leases Vlan951
DHCPOFFER of 192.168.10.x from 192.168.10.1
DHCPACK of 192.168.10.x from 192.168.10.1
bound to 192.168.10.x -- renewal in ... seconds.

$ ip route get 10.90.90.205
10.90.90.205 via 192.168.10.1 dev Vlan951 src 192.168.10.x
```

### 5.3 Stop ZTP Discovery but Keep Vlan951 DHCP

Stop ZTP discovery after the VLAN hook has added the static CPU/FDB entries. This avoids the 300-second `interfaces-config` restart loop.

```text
$ systemctl stop ztp
$ systemctl is-active ztp || true
inactive

# Remove physical-port DHCP clients. Avoid pkill inside SSH one-liners; kill exact PIDs.
$ for p in $(pgrep -f '^/sbin/dhclient .*Ethernet11|^dhclient .*Ethernet11'); do kill "$p"; done
$ rm -f /run/dhclient.Ethernet11.pid /run/dhclient6.Ethernet11.pid
```

`systemctl stop ztp` can also stop the ZTP-started `Vlan951` dhclient. If no `Vlan951` dhclient remains, start it manually:
```text
$ ps -ef | grep '[d]hclient.*Vlan951' || \
   dhclient -v -pf /run/dhclient.Vlan951.pid -lf /var/lib/dhcp/dhclient.Vlan951.leases Vlan951
```

Expected stable state:
```text
$ systemctl is-active ztp || true
inactive

$ ip -br addr | grep -E 'eth0|Ethernet11|Vlan951'
eth0             UP             192.168.80.174/24
Ethernet11       UP             fe80::...
Vlan951@Bridge   UP             192.168.10.x/24

$ ip route show | grep 10.90.90.205
10.90.90.205 via 192.168.10.1 dev Vlan951

$ ping -c 3 -W 1 192.168.10.1
3 packets transmitted, 3 received, 0% packet loss

$ ping -c 3 -W 1 10.90.90.205
3 packets transmitted, 3 received, 0% packet loss
```

### 5.4 Recovery if DHCP Works but ARP/Ping Fails

If DHCP gets an IP but ARP to `192.168.10.1` fails, the relay may be sending ARP replies but the DUT is not receiving unicast frames on the CPU path.

Symptom:
```text
$ ip neigh show dev Vlan951
192.168.10.1 FAILED

$ tcpdump -i any -e -n 'arp or icmp'
# DUT shows ARP request going out only.
# Relay shows ARP reply leaving Ethernet3.
```

Recovery:
```text
$ systemctl restart swss

# wait until swss/syncd are active again
$ systemctl is-active swss
active
$ systemctl is-active syncd
active

# Re-run ZTP hook. swss restart can clear the CPU/FDB programming added by the hook.
$ systemctl restart ztp
$ journalctl -u ztp --no-pager -n 100 | grep -E 'Create static|CPU port|VLANs from ip link|Adding static|Adding VLAN member|Hook DHCP'
Create static MAC etnry for all vlan.
CPU port (fixed): 63
VLANs from ip link: [951]
Adding static MAC entry...
Adding VLAN member (CPU port 63, tagged)...
Hook DHCP client.

$ systemctl stop ztp

$ ping -c 3 -W 1 192.168.10.1
3 packets transmitted, 3 received, 0% packet loss
```

### 5.5 Reflash Verification on DUT Port 6

* **Date**: 2026-06-22 16:58 UTC
* **Status**: PASS

Verified after reflashing the DUT on console server port 6. Management IP stayed `192.168.80.174`.

Latest re-verification notes:
```text
First attempt:
  ZTP hook ran, but Vlan951 got "No DHCPOFFERS".
  Packet capture showed:
    server sent OFFER
    relay sent OFFER out Ethernet3
    DUT did not see the OFFER

Recovery:
  systemctl restart swss
  wait until swss/syncd active
  systemctl restart ztp
  systemctl stop ztp

Result:
  PASS
```

```text
$ dhclient -v -pf /run/dhclient.Vlan951.pid -lf /var/lib/dhcp/dhclient.Vlan951.leases Vlan951
DHCPOFFER of 192.168.10.104 from 192.168.10.1
DHCPACK of 192.168.10.104 from 192.168.10.1
bound to 192.168.10.104 -- renewal in 558 seconds.

$ ip route show
default via 192.168.10.1 dev Vlan951
10.90.90.205 via 192.168.10.1 dev Vlan951
192.168.10.0/24 dev Vlan951 proto kernel scope link src 192.168.10.104
192.168.80.0/24 dev eth0 proto kernel scope link src 192.168.80.174

$ ip route get 10.90.90.205
10.90.90.205 via 192.168.10.1 dev Vlan951 src 192.168.10.104

$ ping -c 3 -W 1 10.90.90.205
3 packets transmitted, 3 received, 0% packet loss

$ systemctl is-active ztp || true
inactive
```

---

## 6. DHCP Packet Verification

> [!NOTE]
> Captures below are from the SONiC.Wistron.3.1.0 server run (server `192.168.80.180` / es2227, server MAC `00:11:22:33:44:01`, lease `192.168.10.104`). MACs/IP can differ between runs but the relay path is identical.

Server-side capture (Vlan2) — relayed DISCOVER in, OFFER out (REQUEST/ACK follow the same path):
```text
$ tcpdump -i Vlan2 -e -n -vvv 'udp port 67'

# relay -> server : client's broadcast relayed as unicast to the DHCP server
5c:ff:35:e7:00:b9 > 00:11:22:33:44:01, ethertype IPv4, length 410
10.90.90.11.67 > 10.90.90.205.67
BOOTP/DHCP Request from 5c:ff:35:e9:55:12, hops 1, xid 0xdcb7895d
  Gateway-IP 192.168.10.1
  Client-Ethernet-Address 5c:ff:35:e9:55:12
  DHCP-Message Discover
  Agent-Information (82): Circuit-ID "sonic:Eth3", Remote-ID 5c:ff:35:e7:00:b9

# server -> relay : OFFER (Reply) back toward the gateway 192.168.10.1
00:11:22:33:44:01 > 5c:ff:35:e7:00:b9, ethertype IPv4, length 363
10.90.90.205.67 > 192.168.10.1.67
BOOTP/DHCP Reply, xid 0xdcb7895d
  Your-IP 192.168.10.104
  Gateway-IP 192.168.10.1
  Client-Ethernet-Address 5c:ff:35:e9:55:12
  DHCP-Message Offer
  Server-ID 10.90.90.205
  Lease-Time 1200
  Subnet-Mask 255.255.255.0
  Default-Gateway 192.168.10.1
  Domain-Name "test.ntc.lab"
  Domain-Name-Server 8.8.8.8
```

Relay-side capture (`-i any`) — full per-interface path of one DISCOVER/OFFER cycle:
```text
$ tcpdump -i any -e -n 'udp port 67 or udp port 68'

# 1. client broadcast DISCOVER arrives tagged on Vlan951
Vlan951 B   5c:ff:35:e9:55:12 ethertype IPv4
0.0.0.0.68 > 255.255.255.255.67  BOOTP/DHCP Request from 5c:ff:35:e9:55:12

# 2. relay forwards it as unicast to the server out Vlan2 / Ethernet1
Vlan2 Out      5c:ff:35:e7:00:b9  10.90.90.11.67 > 10.90.90.205.67  Request
Ethernet1 Out  5c:ff:35:e7:00:b9  10.90.90.11.67 > 10.90.90.205.67  Request

# 3. server OFFER comes back in on Ethernet1 / Vlan2 (server MAC 00:11:22:33:44:01)
Ethernet1 In   00:11:22:33:44:01  10.90.90.205.67 > 192.168.10.1.67  Reply
Vlan2 In       00:11:22:33:44:01  10.90.90.205.67 > 192.168.10.1.67  Reply

# 4. relay broadcasts the OFFER out to the client on Vlan951 / Ethernet3
Vlan951 Out    5c:ff:35:e7:00:b9  192.168.10.1.67 > 255.255.255.255.68  Reply
Ethernet3 Out  5c:ff:35:e7:00:b9  192.168.10.1.67 > 255.255.255.255.68  Reply
```

Relay egress on Ethernet3 confirms the relay-to-client DHCP Reply is an L2 broadcast, and the client's DISCOVER arrives 802.1Q-tagged on vlan 951:
```text
$ tcpdump -i Ethernet3 -e -n 'udp port 67 or udp port 68'

# client -> relay : DISCOVER, tagged vlan 951
5c:ff:35:e9:55:12 > ff:ff:ff:ff:ff:ff, 802.1Q vlan 951, ethertype IPv4
0.0.0.0.68 > 255.255.255.255.67  BOOTP/DHCP Request from 5c:ff:35:e9:55:12

# relay -> client : Reply, untagged L2 broadcast to ff:ff:ff:ff:ff:ff
5c:ff:35:e7:00:b9 > ff:ff:ff:ff:ff:ff, ethertype IPv4
192.168.10.1.67 > 255.255.255.255.68  BOOTP/DHCP Reply
```

This confirms the relay-to-client Reply is delivered as:
* **Ethernet destination MAC**: `ff:ff:ff:ff:ff:ff` (L2 broadcast)
* **IP destination**: `255.255.255.255`
* **DHCP chaddr**: `5c:ff:35:e9:55:12`

The DUT ASIC punted the inbound L2 broadcast DHCP frame to CPU via `SAI_HOSTIF_TRAP_TYPE_DHCP_L2` (COPP `queue4_group3`, `trap_action: trap`). No `ebtables` MAC rewrite was required.

---

## 7. Final Result

* **Result**: PASS

```text
DUT Vlan951:
  192.168.10.104/24

Gateway:
  192.168.10.1

DHCP server identifier:
  10.90.90.205

DHCP server:
  192.168.80.180
  Ethernet9
  Vlan2 10.90.90.205/24
  isc-dhcp-server
  dhcpd listening on UDP/67

Relay:
  192.168.80.179
  Ethernet1 / Vlan2 / 10.90.90.11/24
  Ethernet3 / Vlan951 / 192.168.10.1/24
  DHCP helper 10.90.90.205
  dhcrelay running for Vlan951

DUT:
  192.168.80.174
  SONiC.Wistron_M.1.0.2
  Ethernet11 untagged member of Vlan951
  ZTP started DHCP client on Vlan951

ebtables:
  Not used
  OUTPUT entries = 0
```

---

## 8. Troubleshoot

### 8.1 Root Causes Found During Testing

1. **New DUT had no VLAN configuration.**
   * `Vlan951` did not exist and `Ethernet11` was in routed mode.
   * **Fix**:
     ```text
     config vlan add 951
     config vlan member add 951 -u Ethernet11
     ```

2. **`AMAZON_FLAG` was false on the new DUT.**
   * The ZTP profile `AMAZON` block was skipped.
   * **Fix**:
     ```text
     sed -i 's/AMAZON_FLAG=false/AMAZON_FLAG=true/' /usr/lib/ztp/ztp-profile.sh
     ```

3. **After server reboot, static routes were lost.**
   * Route `192.168.10.0/24 via 10.90.90.11 dev Vlan2` was missing. Without this route, DHCP Offer left through `eth0` instead of `Vlan2`/`Ethernet9`. `dhcpd` was also not running.
   * **Fix**:
     ```text
     sudo ip route replace 192.168.10.0/24 via 10.90.90.11 dev Vlan2
     sudo systemctl restart isc-dhcp-server
     ```

4. **Server Ethernet9 netdev / bridge NOT synced — Vlan2 `LOWERLAYERDOWN`. (MAIN BLOCKER)**
   * Seen on a freshly reflashed server (e.g. SONiC.Wistron.3.1.0 / es2227).
   * `show interfaces status Ethernet9` reports `Oper=up` / `Admin=up`, but the `Ethernet9` kernel netdev is `DOWN` and not enslaved to the bridge, so:
     * `ip -br addr show Vlan2` -> `LOWERLAYERDOWN`
     * `cat /sys/class/net/Vlan2/carrier` -> `0`
     * `ip route ... 10.90.90.0/24` -> shows `"linkdown"`
     * ping from server/relay to the other side -> 100% loss
     * `ip -s link show Ethernet9` -> RX has frames but most are `"dropped"`
   * The ASIC receives frames on `Ethernet9` but the kernel drops them because the netdev is down, so `dhcpd` never sees the relayed `DISCOVER` and never sends an `OFFER`.
   * **Diagnostic**:
     ```text
     ip -br addr show Vlan2 ; cat /sys/class/net/Vlan2/carrier ; cat /sys/class/net/Ethernet9/operstate
     ```
   * **Fix** (bounce the port to re-sync the netdev into the bridge):
     ```text
     sudo config interface shutdown Ethernet9
     sudo config interface startup Ethernet9
     # then verify: cat /sys/class/net/Vlan2/carrier  -> 1
     #              ping -I Vlan2 -c2 10.90.90.11      -> 0% loss
     ```

5. **`SAI_HOSTIF_VLAN_TAG_KEEP` on the DUT is INTERMITTENT (can block DHCP).**
   * When `Vlan951`/`Ethernet11` is added at RUNTIME, the DUT hostif shows `SAI_HOSTIF_ATTR_VLAN_TAG = SAI_HOSTIF_VLAN_TAG_KEEP`. The attribute is ALWAYS `KEEP`, but the actual RX behaviour varies between runs:
     * *Cosmetic case*: DUT binds its `Vlan951` IP fine while showing `KEEP` (RX works).
     * *Blocking case*: `Ethernet11` RX genuinely freezes -> `ip -s link show Ethernet11` RX counter stuck, `show lldp neighbors` loses the `Ethernet11` entry, `dhclient` gets `"No DHCPOFFERS"` even though the relay `tcpdump` shows the `OFFER` leaving its downlink.
   * So DON'T assume it's harmless. When `"No DHCPOFFERS"` AND the server/relay path is verified good (relay sends `OFFER` out its downlink, server has an active lease):
     * **Diagnostic on DUT**:
       ```text
       show lldp neighbors | grep -A4 'Ethernet11'    # missing neighbor => RX frozen
       ip -s link show Ethernet11                     # RX counter not moving => frozen
       ```
     * **Fix**: Reboot the DUT. After boot, LLDP returns on `Ethernet11` (RX healthy) even though the attribute still reads `KEEP`. Then drive ZTP via script.

6. **Getting the IP specifically onto Vlan951 needs the AMAZON ZTP hook.**
   * Fresh DUT may first grab `Ethernet11` as a routed DHCP port. Remove that IP before adding `Ethernet11` as an untagged `Vlan951` member.
   * Use `systemctl restart ztp` to run the `AMAZON` hook. Do NOT use `config ztp run`; it can reload config and erase runtime `Vlan951`.
   * **Confirm the hook ran**:
     ```text
     journalctl -u ztp --no-pager -n 120 | grep -E 'Create static|CPU port|VLANs from ip link|Hook DHCP|Starting DHCP client'
     ```
   * The hook does two important things:
     1. Starts DHCP on `Vlan951`.
     2. Adds the static CPU/FDB programming needed for CPU receive path.
   * After the hook succeeds and `Vlan951` gets an IP, stop ZTP discovery and keep a manual `Vlan951` `dhclient` running for renew.
   * If `systemctl restart swss` is used later, re-run `systemctl restart ztp` once to restore the CPU/FDB programming, then stop ZTP again.

7. **Server/relay setup specifics for SONiC.Wistron.3.1.0 / es2227 / es1227 (Debian 11):**
   * Relay port mapping is 1-based (no `Ethernet0`): uplink to server = `Ethernet1`, downlink to DUT = `Ethernet3`. Confirm with `show lldp neighbors`.
   * Remove ALL default routed-port IPs (`10.0.0.x/31`) on the relay or the `dhcp_relay` container's `wait_for_intf.sh` hangs forever (start stays `RUNNING`, relay program `STOPPED Not started`). After removing + save, `systemctl reset-failed dhcp_relay` (repeated restarts hit the systemd start-limit) then `systemctl restart dhcp_relay`.
   * `config ztp disable -y` on the server can change its `eth0` mgmt DHCP lease (IP moves); recover via console (port 4) and `ip neigh flush <ip>` on the jump host. Prefer a static `MGMT_INTERFACE` to avoid drift.

### 8.2 SWSS / Marvell SAI Online Swap Check

* **Test Date**: 2026-06-25
* **Objective**: Check whether replacing the Marvell SAI package in the running `syncd` container can fix the DUT `Ethernet11` ASIC-to-CPU RX issue.
* **Result**: **Do not use `mrvllibsai_1.16.1-3_arm64.deb` as a hot-fix on this image.** It made the front-panel port/hostif state worse: LLDP did not recover, `Ethernet11` disappeared from Linux netdevs, and `show int status` temporarily returned only the header.

Swap execution and results:
```text
$ docker exec syncd dpkg -s mrvllibsai | grep -E 'Package|Version'
Package: mrvllibsai
Version: 1.15.1-1

$ docker exec syncd sha256sum /usr/lib/libsai.so
41a150e506756fd774526ec7387046e0bb382f2dbd9e3ef0570bdb5c6fa14ad8  /usr/lib/libsai.so

$ tar -C /tmp -cf - mrvllibsai_1.16.1-3_arm64.deb | \
   docker exec -i syncd sh -lc 'cd /tmp && tar -xf -'

$ docker exec syncd dpkg -i /tmp/mrvllibsai_1.16.1-3_arm64.deb
Unpacking mrvllibsai (1.16.1-3) over (1.15.1-1) ...
Setting up mrvllibsai (1.16.1-3) ...

$ docker exec syncd dpkg -s mrvllibsai | grep -E 'Package|Version'
Package: mrvllibsai
Version: 1.16.1-3

$ docker exec syncd sha256sum /usr/lib/libsai.so
280c5fae7c6758b9f94262095ad1d334c66c29d9097d42c8c69f6b96b670366e  /usr/lib/libsai.so

$ docker exec syncd supervisorctl restart syncd
syncd: stopped
syncd: started

$ ip -br link show Ethernet11
Device "Ethernet11" does not exist.

$ bridge vlan show | grep -E 'Bridge|Ethernet11|951'
Bridge            951

$ show lldp table
LocalPort    RemoteDevice    RemotePortID       Capability      RemotePortDescr
-----------  --------------  -----------------  ------------  -----------------
eth0                         f8:60:f0:a6:6b:e2  B                              23
```

**Conclusion**: After the in-container SAI update, the DUT still could not receive LLDP on `Ethernet11`. The `Ethernet11` Linux netdev also disappeared, so this state cannot be used to validate the DHCP relay path.

### 8.3 Full SWSS Restart Test

```text
$ docker tag docker-syncd-mrvl-prestera:latest docker-syncd-mrvl-prestera:sai-1.15.1-backup
$ docker commit syncd docker-syncd-mrvl-prestera:latest
$ systemctl restart swss

$ docker exec syncd dpkg -s mrvllibsai | grep -E 'Package|Version'
Package: mrvllibsai
Version: 1.16.1-3

$ show int status | sed -n '1,5p'
   Interface    Lanes    Speed    MTU    FEC    Alias    Vlan    Oper    Admin    Type    Asym PFC
-----------  -------  -------  -----  -----  -------  ------  ------  -------  ------  ----------

$ ip -br link show | grep -E 'Ethernet11|Vlan951|Bridge'
Bridge           UP             5c:ff:35:e9:55:12 <BROADCAST,MULTICAST,UP,LOWER_UP>
Vlan951@Bridge   UP             5c:ff:35:e9:55:12 <BROADCAST,MULTICAST,UP,LOWER_UP>
```

**Conclusion**: With `1.16.1-3`, `swss`/`syncd` did not rebuild the front-panel port table correctly. This matches the observed failure: LLDP completely stops working because ASIC-to-CPU traffic for the `Ethernet11` path is not delivered to the host.

### 8.4 Revert and Recovery

```text
$ docker tag docker-syncd-mrvl-prestera:sai-1.15.1-backup docker-syncd-mrvl-prestera:latest
$ systemctl stop swss syncd
$ docker rm -f syncd swss teamd
$ systemctl reset-failed swss syncd
$ systemctl start swss

$ docker exec syncd dpkg -s mrvllibsai | grep -E 'Package|Version'
Package: mrvllibsai
Version: 1.15.1-1

$ show int status | grep -E 'Interface|Ethernet11'
   Interface    Lanes    Speed    MTU    FEC    Alias    Vlan    Oper    Admin            Type    Asym PFC
 Ethernet11       10    1000M   9100   none    Eth11   trunk      up       up            RJ45         N/A

$ bridge vlan show | grep -E 'Bridge|Ethernet11|951'
Bridge            951
Ethernet11        951 PVID Egress Untagged

$ show lldp table
LocalPort    RemoteDevice    RemotePortID       Capability    RemotePortDescr
-----------  --------------  -----------------  ------------  -----------------
Ethernet11   sonic           Eth3               BR            Ethernet3
eth0                         f8:60:f0:a6:6b:e2  B             23
```

**Conclusion**:
1. `1.16.1-3` is not a usable hot-fix for this image.
2. The failure is below ZTP/`dhclient`: LLDP also disappears, so it is an ASIC/SAI/hostif CPU RX path problem.
3. If testing a newer SAI is required, build a complete image with a matching SAI/EZB/CPSS set instead of replacing only libsai inside the running syncd container.
4. Local git history shows `1.16.1-3` was previously added and later reverted back to `1.15.1-1`, so the revert is consistent with this runtime result.

### 8.5 Troubleshoot Command Execution Logs

```

$ `show vlan brief` (initial state on new DUT)

| VLAN ID | IP Address | Ports | Port Tagging | Proxy ARP | DHCP Helper Address |
| :---: | :--- | :--- | :--- | :---: | :--- |
| (無) | | | | | |

# Result: No VLANs configured. Vlan951 and Ethernet11 membership missing.

VLAN 設定指令：
```text
$ config vlan add 951
$ config vlan member add 951 -u Ethernet11
```

$ `show vlan brief` (Vlan951 member added)

| VLAN ID | IP Address | Ports | Port Tagging | Proxy ARP | DHCP Helper Address |
| :---: | :--- | :--- | :--- | :---: | :--- |
| 951 | | `Ethernet11` | untagged | disabled | |

# Result: Ethernet11 became untagged member of Vlan951.

路由變更檢測：
```text
default via 192.168.80.254 dev eth0
10.90.90.0/24 dev Vlan2 proto kernel scope link src 10.90.90.205

# Result: Route 192.168.10.0/24 missing. DHCP Offer would egress eth0 instead of Vlan2.

$ ip route replace 192.168.10.0/24 via 10.90.90.11 dev Vlan2
$ ip route show | grep -E '10.90.90|192.168.10|default'
default via 192.168.80.254 dev eth0
10.90.90.0/24 dev Vlan2 proto kernel scope link src 10.90.90.205
192.168.10.0/24 via 10.90.90.11 dev Vlan2

# Result: Route restored. DHCP Offer egresses Vlan2/Ethernet9 to relay correctly.
```

---

## 9. Notes

### 9.1 Key Gotchas (Read This First on a Fresh Reflash)

These lessons were learned the hard way across several reflash cycles. Following them makes the test PASS without rediscovery:

1. **Pick the right DHCP-server deb set by image / Debian base.**
   * `SONiC.Wistron_M.1.0.2` (es1227, Debian 12 / bookworm, libc6 >= 2.36) -> `isc_dhcp/provided/` (4.4.3).
   * `SONiC.Wistron.3.1.0` (es2227, Debian 11 / bullseye, libc6 2.31) -> `isc_dhcp/debs/` (4.4.1).
   * On Debian 11 you MUST also restore `/lib/lsb/init-functions` from `isc_dhcp/scripts/init-functions.lsb` (the bundled `lsb-base_11.6_all.deb` is a dummy that deletes it; otherwise dhcpd init fails: "cannot open /lib/lsb/init-functions").

2. **Server Vlan2 LOWERLAYERDOWN -> bounce Ethernet9.**
   * `show interfaces status Ethernet9` may say oper up while the kernel netdev is down and not in the bridge (Vlan2 carrier 0, route "linkdown", ping fails). Fix:
     ```text
     sudo config interface shutdown Ethernet9 && sudo config interface startup Ethernet9
     ```
   * This was the real blocker, not the DUT.

3. **`SAI_HOSTIF_VLAN_TAG_KEEP` on the DUT is INTERMITTENT — it sometimes blocks DHCP.**
   * The attribute always shows KEEP, but its effect varies:
     * Sometimes the DUT binds fine while showing `KEEP` (RX works, `KEEP` is cosmetic).
     * Sometimes RX genuinely freezes: `ip -s link show Ethernet11` RX counter stuck, `show lldp neighbors` loses the `Ethernet11` entry, and DHCP gets `"No DHCPOFFERS"` even though the relay is sending the `OFFER` out its downlink (confirmed by tcpdump).
   * The freeze appears when `Ethernet11` is added to the VLAN at RUNTIME. Reliable clear: **reboot the DUT** — after boot, LLDP returns on `Ethernet11` (RX healthy) even though the attribute still reads `KEEP`. So: if `"No DHCPOFFERS"` AND the relay `tcpdump` shows the `OFFER` leaving the downlink AND `Ethernet11` RX is frozen / LLDP missing -> reboot the DUT.

4. **Trigger the DUT with `systemctl restart ztp`, never `config ztp run`; run config as a scp'd script.**
   * `config ztp run` (and `rm config_db.json` + restart) does a config reload that erases the runtime `Vlan951` config and lets ZTP discovery re-grab `Ethernet11` as a routed port.
   * The first `systemctl restart ztp` may fire before the box is ready and skip the `AMAZON` block (`add_macentry.py` runs but "Hook DHCP client" never appears) — restart ztp again.
   * DUT `config vlan ...` commands briefly bounce networking and drop your SSH session mid-sequence. Put the whole sequence (remove routed IP, vlan add, member add, `AMAZON_FLAG`, `systemctl restart ztp`) into ONE script, scp it, and run it with `echo <pw> | sudo -S bash /tmp/script.sh` so it executes atomically on the box.
   * ZTP discovery actively re-adds `Ethernet11` as a routed DHCP port in a loop; it will fight your VLAN config. Get `Vlan951` + the `systemctl restart ztp` in before the next scan.
   * Disable ZTP entirely on the SERVER (`config ztp disable -y`).

5. **Relay port numbering may be 1-based; identify uplink/downlink by LLDP, not by number.**
   * On `es1227_54ts` (SONiC.Wistron.3.1.0) `Ethernet0` does NOT exist (1-based mapping), so the roles shift +1 vs older relays: uplink to server = **Ethernet1**, downlink to DUT = **Ethernet3** (were `Ethernet0` / `Ethernet2`). Always confirm with `show lldp neighbors` (match the neighbor's PortDescr/ChassisID) before configuring VLANs.
   * On a freshly reflashed relay the default config has every front port as a routed T2 interface (`ARISTAxxT2`, `10.0.0.x/31`). This makes the `dhcp_relay` container hang: its `start` program runs `wait_for_intf.sh`, which waits for EVERY one of those ~50 routed interfaces to be operationally up — but unconnected ports never come up, so the `isc-dhcpv4-relay-VlanXXX` program stays `STOPPED  Not started` forever. Fix: remove ALL the default routed-port IPs so `wait_for_intf.sh` only waits for `Vlan2`/`Vlan951`:
     ```text
     for p in $(sudo sonic-cfggen -d --var-json INTERFACE | grep -oE 'Ethernet[0-9]+\|[0-9.]+/[0-9]+'); do
       sudo config interface ip remove "${p%%|*}" "${p##*|}"; done
     sudo config save -y
     sudo systemctl reset-failed dhcp_relay   # repeated restarts hit systemd start-limit
     sudo systemctl restart dhcp_relay
     ```
   * Verify: `docker exec dhcp_relay supervisorctl status` -> `isc-dhcpv4-relay-VlanXXX` RUNNING, `start` EXITED.

6. **Mgmt IP drifts after `config ztp disable` / reboot — keep console handy, flush ARP.**
   * `eth0`'s mgmt IP comes from ZTP DHCP; `config ztp disable -y` / reboot can re-lease a DIFFERENT IP (e.g. server moved `192.168.80.167` -> `.166`) and the box appears "down". The box is usually fine (relay still sees it via LLDP, link up) — only the mgmt IP moved.
   * Find the real IP via console (console server `ssh admin@192.168.80.135`, server=port 4, relay=port 5, client=port 6; login `admin` / `Prestera123` ; `ip -br addr show eth0`).
   * On the jump host, a stale ARP can hide the new IP: `sudo ip neigh flush <ip>` then ping.
   * To avoid this entirely, set a static mgmt IP (`MGMT_INTERFACE`) on server/relay. Console is only worth it for this mgmt-IP recovery; for everything else SSH + scp scripts are faster than driving the console via pexpect.

### 9.2 Troubleshooting Order (Check the SERVER First)

Every "No DHCPOFFERS" failure in this test traced back to the **server side**, never to the DUT. When the DUT does not get an IP, check in this order:

1. **Server Vlan2 link** — `ip -br addr show Vlan2` must not be `LOWERLAYERDOWN`; `cat /sys/class/net/Vlan2/carrier` must be `1`. If down, bounce `Ethernet9` (`config interface shutdown/startup Ethernet9`). See Section 8.1 root cause 4.
2. **Server route + dhcpd** — `192.168.10.0/24 via 10.90.90.11 dev Vlan2` present, and `systemctl is-active isc-dhcp-server` is `active`. Both are lost on reboot.
3. **Relay → server reachability** — `ping -I Vlan2 10.90.90.11` from the server is 0% loss.
4. Only then look at the DUT.

### 9.3 SAI_HOSTIF_VLAN_TAG_KEEP is a Red Herring (Do Not Chase It)

On `Wistron_M.1.0.2`, a port added to a VLAN at runtime shows `SAI_HOSTIF_ATTR_VLAN_TAG = SAI_HOSTIF_VLAN_TAG_KEEP` in the ASIC hostif table, and `ip -s link show <port>` shows dropped RX frames. This was initially blamed for DHCP failures, but the DUT **bound 192.168.10.100 on Vlan951 while Ethernet11 still showed VLAN_TAG_KEEP** — so it does NOT block DHCP. Its only real effect is cosmetic: tagged LLDP/multicast frames delivered to an untagged member's netdev are dropped, so `show lldp neighbors` may lose entries. Harmless for this test; fix the server path instead.

### 9.4 Ebtables Not Used

The relay-side `ebtables` DNAT workaround is NOT used in this test.

Old workaround (not used):
```bash
ebtables -t nat -A OUTPUT -o Ethernet3 -p IPv4 \
  --ip-proto udp --ip-sport 67 --ip-dport 68 \
  -j dnat --to-destination <DUT_MAC> --dnat-target ACCEPT
```

Why L2 broadcast DHCP now reaches DUT CPU without `ebtables`:
* Patch applied to image: `0002-Add-dhcp_l2-dhcpv6_l2-to-copp-supported-list.patch`
* Changes in `src/sonic-swss/orchagent/copporch.cpp`:
  * `trap_id_map`: added `{"dhcp_l2", SAI_HOSTIF_TRAP_TYPE_DHCP_L2}`
  * `default_supported_trap_ids`: added `SAI_HOSTIF_TRAP_TYPE_DHCP_L2`
* `copp_cfg.j2` dhcp group:
  * `"trap_ids": "dhcp,dhcpv6,dhcp_l2,dhcpv6_l2"`
* **Result**:
  * ASIC programs `SAI_HOSTIF_TRAP_TYPE_DHCP_L2` via `orchagent`/`syncd`.
  * L2 broadcast DHCP (dst MAC `ff:ff:ff:ff:ff:ff`) is punted to CPU directly.
  * No `ebtables` MAC rewrite needed.