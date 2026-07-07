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
# NOTE: option dhcp-renewal-time / dhcp-rebinding-time are REQUIRED.
# Without them, dhclient on the DUT can end up with renew == rebind == expire
# in its lease file (no distinct T1/T2), so it never attempts an early unicast
# renew or a broadcast rebind before hard expiry -- see Section 8.6.
sudo tee /etc/dhcp/dhcpd.conf << 'EOF'
authoritative;
default-lease-time 1200;
max-lease-time 1200;
option dhcp-renewal-time 600;      # T1 = 50% of lease-time
option dhcp-rebinding-time 1050;   # T2 = 87.5% of lease-time
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

Install a generic hook. Do not write this as `interface = Vlan951`; the route problem applies to any DHCP data-path interface.

**Reason:**
* DHCP server identifier: `10.90.90.205`
* **Without this hook**: DHCP renew unicast to `10.90.90.205` follows the default route, usually `eth0` -> renew `ACK` is not received on `Vlan951` -> lease can expire.
* **With this hook**: `dhclient` receives `BOUND`/`RENEW`/`REBIND`/`REBOOT` -> add host route to the DHCP server via the DHCP router option -> `10.90.90.205/32 via 192.168.10.1 dev Vlan951`.

> [!WARNING]
> **The interface match must cover `Ethernet*` too, not just `Vlan*`** (updated 2026-07-06). On any boot where `Vlan951` doesn't exist yet, ZTP legitimately DHCPs `Ethernet11` as a routed port — a `Vlan*`-only hook never fires there, no route to the ZTP/DHCP server gets added, and the option-67 download times out forever (see `ztp_option67_sop.md` §2.1/§4.4(4)). An earlier narrow version of this snippet caused exactly that regression when it was re-installed from this document after the widened fix had already been applied on the DUT — this template is the source of truth, keep it widened.

Create `/etc/dhcp/dhclient-exit-hooks.d/vlan_dhcp_server_route`:
```sh
#!/bin/sh

case "$reason" in
  BOUND|RENEW|REBIND|REBOOT)
    if echo "$interface" | grep -qE '^(Vlan|Ethernet)' && \
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
    if echo "$interface" | grep -qE '^(Vlan|Ethernet)' && \
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

### 8.5 Missing `dhcp-renewal-time`/`dhcp-rebinding-time` — Ping Drops After ~Lease-Time

* **Test Date**: 2026-07-02
* **Symptom**: `Vlan951` gets an IP and pings the DHCP server fine, then loses connectivity again roughly at the lease-time boundary (observed ~900-1200s after bind, matching `default-lease-time`).

**Root cause**:

`/etc/dhcp/dhcpd.conf` set `default-lease-time`/`max-lease-time` but did **not** set `option dhcp-renewal-time` (T1) or `option dhcp-rebinding-time` (T2). Without explicit T1/T2, the DUT's `dhclient` lease file recorded `renew`/`rebind`/`expire` as the **same timestamp**:

```text
$ cat /var/lib/dhcp/dhclient.Vlan951.leases
lease {
  interface "Vlan951";
  fixed-address 192.168.10.102;
  ...
  renew 5 2002/06/07 05:58:49;
  rebind 5 2002/06/07 05:58:49;
  expire 5 2002/06/07 05:58:49;
}
```

Normal behavior should split these ~50% (T1, unicast RENEW) / ~87.5% (T2, broadcast REBIND) / 100% (EXPIRE) into the lease-time. With all three collapsed to the expiry instant, `dhclient` has no early-renewal window and no broadcast-rebind fallback window — it only gets one shot at unicast RENEW right at expiry, and if that single REQUEST doesn't get a DHCPACK, the IP is dropped and `dhclient` must restart a full broadcast DISCOVER cycle (relies on `dhcp_relay` again) or, worse, go silent without retrying:

```text
$ ps -o pid,etimes,cmd -p 25542
    PID ELAPSED CMD
  25542    1741 /sbin/dhclient -pf /run/dhclient.Ethernet11.pid -lf /var/lib/dhcp/dhclient.Ethernet11.leases Ethernet11 -nw
# no log activity for 29 minutes after the malformed lease's single expiry-time RENEW attempt failed
```

Separately, `journalctl -u isc-dhcp-server` on the server (`192.168.80.161`) confirmed the unicast `DHCPREQUEST` (T1-equivalent, sent to the `dhcp-server-identifier` `10.90.90.205`) was going completely unanswered for 5+ minutes straight:

```text
$ sudo grep -i "Vlan951" /var/log/syslog | grep -iE "DHCPREQUEST|DHCPACK"
DHCPREQUEST for 192.168.10.103 on Vlan951 to 10.90.90.205 port 67   # x15, 06:26:37 - 06:31:49, zero DHCPACK
```

**Fix** (on the DHCP server, `192.168.80.161`):

```text
sudo sed -i '/^max-lease-time 1200;/a option dhcp-renewal-time 600;\noption dhcp-rebinding-time 1050;' /etc/dhcp/dhcpd.conf
sudo dhcpd -t -cf /etc/dhcp/dhcpd.conf   # syntax check, no output = OK
sudo systemctl restart isc-dhcp-server
```

**Verification** (short lease-time used temporarily to observe two full cycles quickly — `default-lease-time 180; option dhcp-renewal-time 90; option dhcp-rebinding-time 150;`):

```text
$ sudo tail -f /var/log/syslog | grep -iE "DHCPREQUEST.*Vlan951|bound.*renewal|DHCPACK"
[t=40s]  DHCPACK of 192.168.10.104 from 10.90.90.205
         bound to 192.168.10.104 -- renewal in 77 seconds.      # T1 renew #1: clean unicast ACK
[t=160s] DHCPREQUEST for 192.168.10.104 on Vlan951 to 10.90.90.205 port 67
         DHCPACK of 192.168.10.104 from 10.90.90.205
         bound to 192.168.10.104 -- renewal in 68 seconds.      # T1 renew #2: clean unicast ACK

# IP 192.168.10.104 stayed bound continuously for the full 200s test window
```

Both T1 renewal cycles completed cleanly via unicast to `10.90.90.205` once the server advertised explicit T1/T2 — a sharp contrast to the ~15 unanswered unicast `DHCPREQUEST`s seen before the fix. After verification, `dhcpd.conf` was restored to production values (`default-lease-time 1200; option dhcp-renewal-time 600; option dhcp-rebinding-time 1050;`), see Section 3.1.

**Note**: this is a different root cause from Section 5.2's route hook. The route hook fixes unicast RENEW packets egressing the wrong interface (`eth0` instead of `Vlan951`) due to the default route; this fix (explicit T1/T2) makes `dhclient` actually schedule a renewal attempt with headroom before hard expiry, and gives it a broadcast-REBIND fallback (via `dhcp_relay`, same path as the initial DISCOVER) if the unicast RENEW is ever dropped. Both fixes are required together for a stable long-running `Vlan951` lease.

### 8.6 Troubleshoot Command Execution Logs

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

### 8.7 Ethernet11 RX Freeze — What Is Proven, What Is Suspected, and Forensics SOP

* **Date**: 2026-07-02
* **Status**: root cause NOT pinned down to the SAI/CPSS internal level yet. This section records the evidence, the suspected mechanism, and the forensic steps to run **during the next freeze, BEFORE recovering**, so the root cause can be definitively identified.

**Observed episode (2026-07-02)**: DUT lost its `Vlan951` DHCP lease and could not re-acquire. Server (`192.168.80.161`) journal showed it answering every relayed DISCOVER with an OFFER (`DHCPOFFER on 192.168.10.105 ... via 192.168.10.1`), but on the DUT `tcpdump -i Ethernet11` showed **only outgoing DISCOVERs, zero inbound frames of any kind for 25s**, and `show lldp neighbors` had lost the Ethernet11 entry.

**Proven facts**:

1. The freeze is in the **ASIC→CPU punt path**, not kernel/dhclient: link up, TX fine, but *all* CPU-bound classes die together — LLDP (COPP trap), broadcast DHCP (`dhcp_l2` trap), and flooded traffic. Same conclusion as §8.4 ("LLDP also disappears, so it is an ASIC/SAI/hostif CPU RX path problem").
2. Trigger correlates with **runtime VLAN/port membership churn**. §9.1-3 already documented the freeze appearing when Ethernet11 is added to the VLAN at runtime. In this episode, the amplifier was the **ZTP discovery 316-second flap loop**: `sonic-ztp: "Restarting network discovery."` every ~316s → restarts `interfaces-config.service` → kills dhclients + bounces eth0 AND the Ethernet11 bridge port (`entered disabled state` → `blocking` → `forwarding` in dmesg, exact 316s period). ZTP never completes because the lab DHCP server offers no provisioning data (option 67 / ztp_data_url), so it loops forever — ~11 bounce cycles over 58 minutes preceded the freeze.
3. `systemctl restart swss` (rebuilds all SAI state) followed by one `systemctl restart ztp` (re-runs the AMAZON hook) reliably recovers; cold reboot also recovers. Hardware is fine.
4. This SAI family has prior CPU-punt bugs: 1.17.1-2 fixed VLAN-based→port-based ARP trapping (SAIPRST-5514).

**Suspected mechanism** (structural, unproven): the CPU RX path on this platform is two layers stacked:

* SAI/orchagent-owned: COPP traps (lldp, dhcp_l2, ...) + hostif delivery.
* AMAZON-hook-owned, programmed **out-of-band via CPSS telnet (localhost:12345 in syncd)**: `cpssDxChBrgVlanMemberAdd` (CPU port 63 as tagged member of VLAN 951) + static FDB → CPU63. **SAI/orchagent cannot see these entries.**

Every interface bounce makes orchagent re-program VLAN membership / flush FDB; it can silently overwrite the out-of-band CPU63 entries (killing the unicast-to-CPU path). But trap-based classes (LLDP/dhcp_l2) dying too means SAI's own hostif/SDMA RX state also wedges under repeated churn — that part is a SAI-internal bug we cannot fix from outside.

**Forensics SOP — run these DURING the next freeze, BEFORE any recovery** (recovery destroys the evidence):

```text
# 0. Confirm it is the freeze (not server-side): TX-only tcpdump + LLDP gone
sudo timeout 20 tcpdump -i Ethernet11 -n            # expect: outgoing only, no inbound
show lldp neighbors | grep -A4 Ethernet11            # expect: empty

# 1. From inside syncd, check CPSS state via the CPSS shell
docker exec -it syncd bash
telnet localhost 12345
# 1a. Is CPU port 63 still a member of VLAN 951?
#     (dump VLAN entry / port membership for vid 951)
# 1b. Read SDMA/trap-queue counters, then send pings from the peer and read again:
#     - counters not moving  -> ASIC is dropping before punt (VLAN member / FDB / trap entry lost)
#     - counters moving but no frames on the netdev -> hostif/SDMA delivery wedge (SAI internal)

# 2. Interpretation
#    CPU63 missing from VLAN 951  => orchagent overwrote the out-of-band hook programming
#                                    (workaround architecture problem; hook must be re-applied
#                                     after ANY VLAN/port reprogramming, or moved into SAI-visible config)
#    CPU63 present, counters dead => SAI hostif/SDMA RX wedge; collect CPSS dump and open a
#                                    Marvell case (reference SAIPRST-5514 family)

# 3. Only after evidence is captured, recover:
systemctl restart swss      # wait swss/syncd active
systemctl restart ztp       # re-run AMAZON hook (CPU63 FDB + VLAN member + DHCP)
systemctl stop ztp          # kill the 316s discovery flap loop (see below)
# stop ztp ALSO kills the ztp-started Vlan951 dhclient -> restart it manually:
dhclient -pf /run/dhclient.Vlan951.pid -lf /var/lib/dhcp/dhclient.Vlan951.leases Vlan951 -nw
# and remove the stray physical-port dhclients started by interfaces-config:
for p in $(pgrep -x dhclient -a | grep Ethernet11 | awk '{print $1}'); do kill "$p"; done
```

**Stability note (runtime-only state)**: the stable state (`ztp` stopped + manual `Vlan951` dhclient) does NOT survive a reboot. After any reboot, ZTP re-enters Active Discovery and the 316s flap loop returns — re-run the recovery sequence above (automated in `/home/otis/recover_ztp.sh` + `/home/otis/fix_dhclient.sh` on the build host). The permanent fix is to give ZTP real provisioning data (DHCP option 67 → ztp.json URL) so it completes and exits discovery on its own.

**Pitfall when checking dhclient remotely**: `ps -ef | grep '[d]hclient.*Vlan951'` self-matches the wrapping `bash -c` process when run through ssh one-liners, producing false positives (this masked the killed dhclient once). Use `pgrep -x dhclient -a | grep Vlan951` instead (`-x` = exact process-name match, cannot match the wrapper).

**Correction to the suspected mechanism above (2026-07-02, confirmed by reading the actual hook source)**: the AMAZON hook scripts (`add_StaticFDBEntry_CPU63_from_iplink.py` / `_onVlan951.py` in `wistron_patches/ztp_workaround/0002-ztp-workaround-mac-table-added.patch`) only ever **add** the CPU63 static FDB entry and VLAN member via CPSS telnet on every ZTP cycle — they never explicitly delete or re-add VLAN membership themselves. `hook_dhcp_for_vlans` (also in that patch) only checks VLAN/port `show` output and launches `dhclient`; it does not touch VLAN config either.

The actual port-bounce mechanism is `systemctl restart interfaces-config`, invoked from two places:
1. Stock upstream `sonic-ztp`'s own discovery-restart logic (not in the Wistron patch) — this is the source of the `"Restarting network discovery."` log line and the 316s flap period.
2. `ztp-profile.sh`'s own `resume`/`remove` code paths (`"Restarting network configuration."`), which are in the Wistron patch.

`interfaces-config.service` restart toggles `Ethernet11`'s Linux admin state down/up as part of re-applying `config_db.json`, and that admin toggle is what produces the `disabled -> blocking -> forwarding` bridge-port state cycle seen in dmesg — not a VLAN-membership delete/re-add by the hook itself. Whether `orchagent`'s post-bounce VLAN/bridge-port resync (triggered by the admin toggle) clobbers the hook's out-of-band CPU63 entry remains **unconfirmed** — the Forensics SOP above is still the only way to pin this down definitively.

### 8.7.1 Forensics Run 2026-07-02: Freeze Reproduced, Root Cause Narrowed to SAI-Internal Wedge

* **Date**: 2026-07-02
* **Method**: deliberately reproduced the freeze by tight-looping `systemctl restart interfaces-config` every ~4s (the confirmed bounce mechanism from §8.7's correction above) while polling `show lldp neighbors` + `ping 192.168.10.1` after each iteration. **Froze in 2 iterations** (~8s) — far faster than the naturally-occurring 316s-interval/11-cycle/58-minute episode.

> [!IMPORTANT]
> **The trigger is NOT deterministic.** A second run of the same 20-iteration loop later the same day (run manually on the DUT, after a fresh `restart swss` + manual hook re-add from the recovery below) completed **all 20 iterations with no freeze** — LLDP present, ping OK, and `0/63 tagged` intact on every check. Three data points so far: froze after ~11 bounces (natural episode), froze after 2 (first deliberate run), survived 20 (second deliberate run). Each bounce is a probabilistic race, with at least two suspected (unproven) covariates worth recording in future runs:
> 1. **SAI state age**: the freeze runs happened on an swss instance that had been up ~80 min and had absorbed 3 ZTP cycles + a link-down incident; the clean 20-iteration run happened minutes after a fresh `restart swss` (clean SAI init). Record `systemctl show swss --property=ActiveEnterTimestamp` at test start.
> 2. **Concurrent punt traffic during the bounce window**: the natural episode had heavy ZTP DHCP broadcast activity in flight; the clean run had only idle LLDP/renew traffic. The race may require a frame actually traversing the punt path at the moment of reprogramming — consider running a continuous ping from the relay toward the DUT during the loop.
> To increase reproduction odds: raise `MAX_ITER` to 50, drop the inter-iteration sleep to 2s, and add inbound traffic.

**CPSS commands discovered for this forensics SOP** (the CLI is a Marvell LuaCLI shell on `localhost:12345`; ztp's own hook scripts use `/usr/lib/ztp/telnetlib.py`, a vendored copy, since stdlib `telnetlib` is removed in this image's Python 3.13):
```text
show vlan device 0 tag 951        # non-destructive: lists VLAN 951 port membership + tag mode
                                   # healthy example:
                                   #   951   0/63   tagged     Control   FID     <- CPU port
                                   #         0/10   untagged             <- Ethernet11
```

**Evidence captured DURING the freeze (before any recovery)**:
1. `show vlan device 0 tag 951` — **CPU port 63 was still present as a tagged member** (`0/63 tagged`). VLAN membership was NOT lost.
2. `ip -s link show Ethernet11` RX packet counter — **frozen at exactly 300 packets across 3 consecutive ping attempts** (bytes/packets identical on every read). Proves the kernel netdev genuinely stopped receiving, not just an L3/ARP-layer symptom.
3. `tcpdump -i Ethernet11 -e -n` for 8s — **captured only the DUT's own outgoing LLDP frame** (`5c:ff:35:e9:55:12 > 01:80:c2:00:00:0e`, TX direction). Zero inbound frames of any kind.
4. `ip neigh show dev Vlan951` — empty (no ARP entry at all, not even `FAILED`).
5. `show lldp neighbors` — no `Ethernet11` entry (consistent with prior freeze episodes).

**Conclusion**: per §8.7's own interpretation table (`CPU63 present, counters dead => SAI hostif/SDMA RX wedge`), this run **rules out** the "orchagent overwrote the out-of-band CPU63 entry" hypothesis as the mechanism for the freeze itself — the entry survived. The freeze is a **SAI-internal hostif/SDMA RX delivery wedge**: the ASIC-to-CPU trap path stops delivering frames for reasons internal to `mrvllibsai`, independent of whether the CPSS VLAN table is intact. This matches §8.2-8.4's finding that swapping `mrvllibsai` versions changes port/hostif behavior — the bug lives inside the SAI/CPSS binary, not in the Wistron VLAN/FDB workaround scripts.

> [!NOTE]
> An ASIC-level MIB counter check (`show interfaces mac counters ethernet 0/10`) was also attempted during the freeze but returned all-zero for both RX **and** TX counters, which contradicts the confirmed-working TX (LLDP was seen transmitting via `tcpdump`). This reading is **inconclusive** — likely wrong port index or a read/clear semantic on that counter view — and should not be treated as evidence either way. Do not rely on it without first confirming the port-index mapping and counter-reset behavior independently.

**Recovery required an extra step this time, and it reveals an operational trap**: the standard §8.7 recovery (`restart swss` -> wait active -> `restart ztp` -> `stop ztp`) did **not** clear the freeze. Root cause of the recovery failure:

* Once ZTP has reached a **persisted** `ZTP Status: SUCCESS` (as it now does, via the §10 option-67 fix), `systemctl restart ztp` short-circuits: `journalctl -u ztp` shows only `ZTP already completed with result SUCCESS at ...` and the service exits in ~5s **without re-running the AMAZON hook**. Before the §10 fix, ZTP was permanently stuck in discovery (never reached SUCCESS), so every `restart ztp` naturally re-ran the hook as part of its normal discovery attempt — that's why the old recovery sequence in §8.7 worked reliably back then.
* Net effect: `restart swss` (which unconditionally wipes all SAI state, including the out-of-band CPU63 entry) is no longer paired with anything that restores CPU63, because `restart ztp` is now a no-op. Confirmed via `show vlan device 0 tag 951` after the standard recovery sequence: `0/63` was **completely absent** (not present-but-frozen — actually gone this time, since `restart swss` really does clear it and nothing re-added it).

**Fix**: after `restart swss` on a DUT that has already reached persisted `ZTP Status: SUCCESS`, re-run the AMAZON hook scripts **directly** instead of relying on `systemctl restart ztp`:
```bash
python3 /usr/lib/ztp/add_macentry.py
python3 /usr/lib/ztp/add_StaticFDBEntry_CPU63_from_iplink.py
# then restart the Vlan951 dhclient for a fresh DISCOVER (old lease's unicast RENEW may
# still be racing against the just-restored CPU path):
for p in $(pgrep -x dhclient -a | grep Vlan951 | awk '{print $1}'); do sudo kill "$p"; done
sudo rm -f /run/dhclient.Vlan951.pid
sudo dhclient -v -pf /run/dhclient.Vlan951.pid -lf /var/lib/dhcp/dhclient.Vlan951.leases Vlan951 -nw &
```
Verified this restored `show vlan device 0 tag 951` to `0/63 tagged`, then `ping 192.168.10.1` and `ping 10.90.90.205` both returned 0% loss and `show lldp neighbors` showed `Ethernet11` again.

**Updated recovery decision tree for DUTs that have reached §10's persisted SUCCESS state**:
```text
freeze detected
  -> restart swss, wait for swss+syncd active, settle ~30s
  -> check `show ztp status`:
       ZTP Status != SUCCESS  -> `systemctl restart ztp` re-runs the hook automatically (old behavior)
       ZTP Status == SUCCESS  -> `systemctl restart ztp` is a NO-OP; manually re-run
                                  add_macentry.py + add_StaticFDBEntry_CPU63_from_iplink.py,
                                  then restart the Vlan951 dhclient
```

### 8.7.2 Standalone Runbook — Reproduce the Freeze Directly on the DUT

Self-contained copy-paste version of the §8.7.1 experiment, for running directly in a shell on the DUT (no jump-host wrapper needed). Requires `sudo` and the stock AMAZON hook files under `/usr/lib/ztp/`.

**Prerequisite check**:
```bash
ls /usr/lib/ztp/add_macentry.py /usr/lib/ztp/add_StaticFDBEntry_CPU63_from_iplink.py /usr/lib/ztp/telnetlib.py
```

**Step 1 — CPSS VLAN-membership check script** (non-destructive, pure read):
```bash
cat > /tmp/cpss_check_vlan951.py << 'EOF'
#!/usr/bin/env python3
import sys, time
sys.path.insert(0, "/usr/lib/ztp")
from telnetlib import Telnet

tn = Telnet("localhost", 12345, 10)
time.sleep(1)
tn.read_very_eager()
tn.write(b"show vlan device 0 tag 951\n")
time.sleep(2)
out = tn.read_very_eager()
print(out.decode(errors="replace"))
tn.write(b"CLIexit\n")
tn.close()
EOF
```
Baseline (should show `0/63 tagged` when healthy):
```bash
python3 /tmp/cpss_check_vlan951.py
```

**Step 2 — churn + freeze-detection loop**:
```bash
cat > /tmp/churn_freeze_test.sh << 'EOF'
#!/bin/bash
LOG=/tmp/churn_test.log
MAX_ITER=20
> "$LOG"

echo "=== BASELINE CPU63 ===" | tee -a "$LOG"
python3 /tmp/cpss_check_vlan951.py 2>&1 | tee -a "$LOG"

fail_streak=0
i=0
while [ "$i" -lt "$MAX_ITER" ]; do
  i=$((i+1))
  echo "--- iteration $i: restart interfaces-config ---" | tee -a "$LOG"
  sudo systemctl restart interfaces-config >>"$LOG" 2>&1
  sleep 4

  lldp=$(show lldp neighbors 2>/dev/null | grep -A2 "Interface:.*Ethernet11")
  pingres=$(ping -c1 -W1 192.168.10.1 2>&1)
  ok=$(echo "$pingres" | grep -c "1 received")

  echo "iter=$i lldp_present=$([ -n "$lldp" ] && echo yes || echo no) ping_ok=$ok" | tee -a "$LOG"

  if [ "$ok" -eq 0 ]; then fail_streak=$((fail_streak+1)); else fail_streak=0; fi

  if [ "$fail_streak" -ge 2 ] && [ -z "$lldp" ]; then
    echo "=== FREEZE DETECTED at iteration $i ===" | tee -a "$LOG"
    echo "=== CPSS EVIDENCE (BEFORE RECOVERY) ===" | tee -a "$LOG"
    python3 /tmp/cpss_check_vlan951.py 2>&1 | tee -a "$LOG"
    echo "=== RX counters ===" | tee -a "$LOG"
    ip -s link show Ethernet11 | tee -a "$LOG"
    echo "=== STOPPED. Evidence in $LOG. Do NOT recover yet — see Step 3. ===" | tee -a "$LOG"
    exit 0
  fi
done
echo "=== NO FREEZE after $MAX_ITER iterations ===" | tee -a "$LOG"
EOF
chmod +x /tmp/churn_freeze_test.sh
```
Run it (loops until freeze detected or 20 iterations exhausted):
```bash
rm -f /tmp/churn_test.log    # a stale log from a previous run/user can cause
                             # "Permission denied" on the truncate even as root
bash /tmp/churn_freeze_test.sh
```

> [!NOTE]
> A single clean run does not disprove the trigger — see the §8.7.1 IMPORTANT note: one deliberate run froze in 2 iterations, another survived all 20. Treat each bounce as a probabilistic race; run multiple rounds (raise `MAX_ITER`, shorten the sleep, add inbound traffic from the relay) before concluding anything about reproducibility.

**Step 3 — once frozen, capture extra evidence BEFORE recovering**:
```bash
show lldp neighbors | grep -A4 Ethernet11        # expect: empty
ip neigh show dev Vlan951                        # expect: empty or INCOMPLETE

ip -s link show Ethernet11 | grep -A1 "RX:"
for i in 1 2 3; do ping -c1 -W1 192.168.10.1 >/dev/null 2>&1; done
ip -s link show Ethernet11 | grep -A1 "RX:"      # expect: identical to the line above

sudo timeout 8 tcpdump -i Ethernet11 -e -n        # expect: outgoing frames only

python3 /tmp/cpss_check_vlan951.py                # the key read — see interpretation below
```
**Interpretation**:
* `0/63 tagged` still present + RX counter frozen -> SAI/CPSS-internal hostif/SDMA wedge (§8.7.1's result).
* `0/63` missing entirely -> orchagent overwrote the out-of-band hook programming (the originally suspected mechanism, not observed in §8.7.1's run).

**Step 4 — recovery** (check `show ztp status` first — the path differs depending on whether ZTP has ever reached `SUCCESS`):
```bash
show ztp status | grep "ZTP Status"
```
If **not** `SUCCESS` (still in discovery) — standard §8.7 recovery works, `restart ztp` re-runs the hook automatically:
```bash
sudo systemctl restart swss
systemctl is-active swss syncd   # wait for both "active"
sleep 30
sudo systemctl restart ztp
sleep 15
sudo systemctl stop ztp
```
If `SUCCESS` (e.g. after completing §10's option-67 SOP) — `restart ztp` is a no-op (`journalctl -u ztp` shows `ZTP already completed with result SUCCESS...` and exits in ~5s without touching CPU63). Re-run the hook scripts directly instead:
```bash
sudo systemctl restart swss
systemctl is-active swss syncd   # wait for both "active"
sleep 30

python3 /usr/lib/ztp/add_macentry.py
python3 /usr/lib/ztp/add_StaticFDBEntry_CPU63_from_iplink.py
python3 /tmp/cpss_check_vlan951.py               # confirm 0/63 tagged is back

for p in $(pgrep -x dhclient -a | grep Vlan951 | awk '{print $1}'); do sudo kill "$p"; done
sudo rm -f /run/dhclient.Vlan951.pid
sudo dhclient -v -pf /run/dhclient.Vlan951.pid -lf /var/lib/dhcp/dhclient.Vlan951.leases Vlan951 -nw &
sleep 10
```
**Final verification** (either path):
```bash
ping -c3 -W1 192.168.10.1        # expect 0% loss
ping -c3 -W1 10.90.90.205        # expect 0% loss
show lldp neighbors | grep -A4 Ethernet11   # expect the relay neighbor entry back
```

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
   * 2026-07-02 update: the ZTP discovery 316s flap loop massively amplifies the odds of hitting this freeze, and `restart swss` + `restart ztp` (hook re-run) also clears it without a reboot. **Before recovering, capture CPSS-level evidence — see §8.7 Forensics SOP** (checks whether CPU port 63 lost its VLAN 951 membership vs. a SAI hostif/SDMA wedge).

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

6. **Server `dhcpd.conf` must set `option dhcp-renewal-time`/`option dhcp-rebinding-time` explicitly, or leases silently fail to renew ~lease-time later.**
   * Without them, the DUT's lease file collapses `renew`/`rebind`/`expire` into one timestamp, giving `dhclient` no early-renew window and no broadcast-rebind fallback — ping to the DHCP server drops roughly `default-lease-time` seconds after bind. See Section 8.5.
   * Fix: add `option dhcp-renewal-time 600;` (T1, 50%) and `option dhcp-rebinding-time 1050;` (T2, 87.5%) for a 1200s lease, then `systemctl restart isc-dhcp-server`.

7. **Mgmt IP drifts after `config ztp disable` / reboot — keep console handy, flush ARP.**
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

---

## 10. ZTP HTTP Server + DHCP Option 67 SOP (Production Fix for the 316s Discovery Loop)

* **Date**: 2026-07-02
* **Status**: PASS. `ZTP Status: SUCCESS`, `ZTP Service: Inactive` — no further discovery loop.

### 10.1 Objective

Give ZTP real provisioning data via DHCP option 67 (`ztp_data_url`) so the DUT completes ZTP discovery instead of looping forever with `sonic-ztp: "Restarting network discovery."` every 316s (§8.7 / §9.1-3). This is the permanent fix referenced in those sections.

### 10.2 Architecture

* HTTP server co-located on the DHCP server, `192.168.80.161`, reachable from the DUT over the same `Vlan2`/`Vlan951` relay path already used for DHCP (`10.90.90.205:8080`).
* `192.168.80.161` has **no internet access** — all files must be prepared locally (e.g. WSL) then `scp`'d in via the jump host chain. Do not attempt `apt-get install nginx` etc. directly on it.
* SSH to both the DUT and the DHCP server in this environment goes through a jump host chain (`~/.ssh/config`: `Host jump` -> `Host 71` -> target), and both boxes use password auth only (no key installed).

### 10.3 Server-side setup (`192.168.80.161`)

Directory + files:
```text
~admin/ztp-www/
  ztp.json
  ztp-provisioning.sh
```

**`ztp.json` — correct schema** (the section must live *inside* `"ztp"`, not as a top-level sibling key):
```json
{
  "ztp": {
    "provisioning-script": {
      "plugin": {
        "url": "http://10.90.90.205:8080/ztp-provisioning.sh"
      },
      "reboot-on-success": false,
      "ignore-result": false
    },
    "restart-ztp-no-config": false
  }
}
```

> [!WARNING]
> **Schema gotcha (hit 2026-07-02)**: a top-level `"provisioning-scripts": [...]` (plural, sibling of `"ztp"`) is silently ignored by `sonic-ztp` — it is not an error, the file just gets treated as an empty/no-op profile. Symptom: `journalctl -u ztp` shows `Downloading provisioning data ... ZTP successfully completed` with **no** `Processing configuration section ...` line at all. If that line is missing, the JSON schema is wrong — re-check the nesting before touching DHCP/HTTP.

`ztp-provisioning.sh`:
```bash
#!/bin/bash
exec >> /var/log/ztp.log 2>&1
echo "=== ZTP Provisioning Started ==="
date
show ver 2>/dev/null | head -6
echo "--- saving startup config so ZTP exits discovery ---"
config save -y
echo "=== ZTP Provisioning Completed ==="
date
exit 0
```

> [!WARNING]
> **Why `config save -y` is required**: without a startup `/etc/sonic/config_db.json`, ZTP logs `ZTP completed but startup configuration '/etc/sonic/config_db.json' not found. Waiting for 300 seconds before restarting ZTP.` and keeps re-entering discovery even after `provisioning-script: SUCCESS`. The provisioning script's last step must persist a startup config, or ZTP treats the run as incomplete and loops every 300s anyway.

Start the HTTP server (not managed by systemd — plain `nohup`):
```bash
mkdir -p ~/ztp-www
cp ztp.json ztp-provisioning.sh ~/ztp-www/
chmod 644 ~/ztp-www/ztp.json
chmod 755 ~/ztp-www/ztp-provisioning.sh
cd ~/ztp-www
nohup python3 -m http.server 8080 > /tmp/ztp_http.log 2>&1 &
```

> [!WARNING]
> **Not reboot-persistent.** After any reboot of `192.168.80.161`, re-run the `nohup python3 -m http.server 8080` line above, in addition to the existing §3.2 static-route + `isc-dhcp-server` restart steps.

Verify from the server itself:
```bash
curl -s http://10.90.90.205:8080/ztp.json                                             # returns the JSON above
curl -s -o /dev/null -w "%{http_code}\n" http://10.90.90.205:8080/ztp-provisioning.sh  # 200
```

### 10.4 `dhcpd.conf`: add DHCP option 67

Add `option bootfile-name` (DHCP option 67 — SONiC's `dhclient-exit-hooks.d/ztp` maps this to `ztp_data_url`) inside the `192.168.10.0` subnet block that serves the DUT:
```text
subnet 192.168.10.0 netmask 255.255.255.0 {
  range 192.168.10.100 192.168.10.109;
  option routers 192.168.10.1;
  option subnet-mask 255.255.255.0;
  option domain-name "test.ntc.lab";
  option domain-name-servers 8.8.8.8;
  option bootfile-name "http://10.90.90.205:8080/ztp.json";
}
```

> [!WARNING]
> `option bootfile-name http://...;` **without quotes** fails `dhcpd -t` with `semicolon expected` (the `//` and `:` are parsed as tokens). The value must be a quoted string: `option bootfile-name "http://...";`.

Syntax check + reload:
```bash
sudo cp /etc/dhcp/dhcpd.conf /etc/dhcp/dhcpd.conf.bak.$(date +%s)
sudo dhcpd -t -cf /etc/dhcp/dhcpd.conf     # no output = OK
sudo systemctl restart isc-dhcp-server
```

DUT-side confirms receipt in `/var/run/ztp/`:
```bash
cat /var/run/ztp/dhcp_67-ztp_data_url      # -> http://10.90.90.205:8080/ztp.json
cat /var/run/ztp/ztp_data_opt67.json       # -> the downloaded file, verbatim
```

### 10.5 Full DUT-side verification flow

1. Confirm `AMAZON_FLAG=true` in `/usr/lib/ztp/ztp-profile.sh` (§9.1 gotcha 2 / §5.1).
2. Confirm `Vlan951` exists with `Ethernet11` as an untagged member (§5.1).
3. Poll `show ztp status` — target end state:
   ```text
   ZTP Admin Mode : True
   ZTP Service    : Inactive
   ZTP Status     : SUCCESS
   ZTP Source     : dhcp-opt67 (Vlan951)

   provisioning-script: SUCCESS
   ```
4. `journalctl -u ztp` on a clean cycle shows this sequence:
   ```text
   DHCPACK of <ip> from 192.168.10.1
   Downloading provisioning data from http://10.90.90.205:8080/ztp.json to /var/run/ztp/ztp_data_opt67.json
   Processing configuration section provisioning-script at ...
   Processed Configuration section provisioning-script with result SUCCESS, exit code (0) at ...
   Checking configuration section provisioning-script result: SUCCESS, ignore-result: False.
   ZTP successfully completed at ...
   ```
5. Once `ZTP Service: Inactive` + `ZTP Status: SUCCESS`, verify the DHCP relay chain end-to-end from the DUT:
   ```bash
   ping -c3 192.168.10.1     # DUT -> relay gateway
   ping -c3 10.90.90.205     # DUT -> DHCP/HTTP server, via the §5.2 route hook
   ```

### 10.6 Known failure mode during this SOP: repeated ZTP retries can re-trigger the Ethernet11 freeze/link-down from §8.7

If the ztp.json served on cycle 1 has a schema error (§10.3) or any other reason ZTP doesn't reach `SUCCESS` immediately, ZTP retries roughly every 5-6 minutes. Each retry re-runs the AMAZON hook and can trigger `interfaces-config.service` restarts — the same VLAN/port churn pattern implicated in §8.7. **More retries before `SUCCESS` = more chances to hit the freeze.**

Observed 2026-07-02: the first `ztp.json` had the schema bug above, so ZTP needed 3 cycles (~12 min) to reach `SUCCESS` (the fix was uploaded to the HTTP server mid-cycle-2). After cycle 3 completed, `Ethernet11` came up in kernel `state DOWN` even though `show interfaces status` reported `Oper up / Admin up` — a milder symptom than the full RX-freeze in §8.7 (LLDP was not lost, `ip -s link show` counters were not stuck). Recovery was a single `ip link set Ethernet11 up` (or `config interface startup Ethernet11`) — **check for this cheap fix first** before escalating to the full §8.7 forensics SOP / §5.4 `restart swss` recovery.

See §8.7's added correction note for the (now code-confirmed) mechanism: the AMAZON hook only ever *adds* the CPU63 entry; `interfaces-config.service` restart is what actually toggles `Ethernet11`'s admin state and cycles the bridge port through `disabled -> blocking -> forwarding`.

> [!WARNING]
> **§8.7.1 is required reading once a DUT has reached this SOP's persisted `ZTP Status: SUCCESS`.** The standard §8.7 recovery (`restart swss` + `restart ztp`) silently stops working at that point, because `restart ztp` becomes a no-op (`ZTP already completed with result SUCCESS...`) and no longer re-runs the AMAZON hook that restores the CPU63 VLAN entry `restart swss` just wiped. Use §8.7.1's updated recovery decision tree (manually re-run `add_macentry.py` + `add_StaticFDBEntry_CPU63_from_iplink.py`) instead.

### 10.7 Result Summary (2026-07-02)

```text
DUT:            192.168.80.174, Vlan951 192.168.10.108/24
Relay:          192.168.80.179, unchanged from §7
DHCP+HTTP:      192.168.80.161, Vlan2 10.90.90.205:8080
ztp.json:       http://10.90.90.205:8080/ztp.json (option 67 / bootfile-name)
ZTP result:     ZTP Status: SUCCESS, ZTP Service: Inactive, ZTP Source: dhcp-opt67 (Vlan951)
                provisioning-script: SUCCESS
Connectivity:   ping 192.168.10.1  0% loss
                ping 10.90.90.205  0% loss
```

`config save -y` as the last step of the provisioning script is what lets ZTP treat itself as fully provisioned rather than re-entering the 300s "no startup config" wait loop described in §10.3.