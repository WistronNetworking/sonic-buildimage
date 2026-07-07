# ZTP via DHCP Option 67 — Standalone SOP

Server setup, a DUT-side one-time prerequisite, the file map, and how to re-trigger ZTP after it has completed.
Companion to `Kyoto ztp report.md` §5 (DUT VLAN setup), §8.7.x (RX-freeze forensics), and §10 (original setup narrative); this note is the self-contained, hardened operational version.

Verified 2026-07-03 on:

```text
DUT:          192.168.80.174  SONiC.Wistron_M.1.0.2 (trixie, Debian 13.5, kernel 6.12.41), HwSku wistron_es1227_54ts_p2
DHCP+HTTP:    192.168.80.161  (Vlan2 10.90.90.205/24, isc-dhcp-server + python http.server 8080)
Relay:        192.168.80.179  (Vlan2 10.90.90.11 uplink / Vlan951 192.168.10.1 downlink, helper 10.90.90.205)
DUT data path: Vlan951, untagged member Ethernet11, DHCP pool 192.168.10.100-109
```

Result state when everything works:

```text
ZTP Admin Mode : True
ZTP Service    : Inactive
ZTP Status     : SUCCESS
ZTP Source     : dhcp-opt67 (Vlan951)
provisioning-script: SUCCESS
```

**Two structural gaps were found and are now fixed at the root in §2, instead of being worked around after the fact:**
1. The stock `vlan_dhcp_server_route` hook only matches `Vlan*` interfaces. On any boot where `Vlan951` isn't set up yet (fresh flash, or `config_db.json` missing for any reason), ZTP legitimately grabs `Ethernet11` as a routed DHCP port — and the hook never fires, so the DUT has no route to the ZTP server and `curl` times out forever despite a healthy DHCPACK. §2.1 widens the hook so this can't happen regardless of which interface holds the lease.
2. `ztp-profile.sh` only clears `/var/run/ztp/ztp.lock` in its destructive `remove` path, never on a plain stop/start. Any dhclient killed mid-hook (troubleshooting, churn testing) leaves it stale, after which the option-67 URL silently stops being written on every future lease — DHCP looks perfectly healthy while ZTP loops forever. §4.1's re-trigger procedure now clears this lock unconditionally, every time.

---

## 1. Server setup (192.168.80.161)

The server has **no internet access** — prepare all files locally (e.g. WSL) and `scp` them in through the jump chain (`jump` -> `71` -> target, password auth).

### 1.1 HTTP content

Directory served: `~admin/ztp-www/`, two files.

**`ztp.json`** — the section MUST be nested inside `"ztp"`:

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
> A top-level `"provisioning-scripts": [...]` (sibling of `"ztp"`) is **silently ignored** — ZTP logs `ZTP successfully completed` with no `Processing configuration section` line, runs nothing, then loops every 300s complaining about missing startup config. If the `Processing configuration section` journal line is missing, fix the JSON nesting first.

**`ztp-provisioning.sh`**:

```bash
#!/bin/bash
exec >> /var/log/ztp.log 2>&1
echo "=== ZTP Provisioning Started ==="
date
show ver 2>/dev/null | head -6

echo "--- saving startup config so ZTP exits discovery ---"
# The interface this script runs on may still be settling port/bridge state
# right after DHCP bind (observed: Ethernet11 flapping down for ~1-2s while
# becoming a stable VLAN951 bridge slave). `config save -y` can fail SILENTLY
# if it races that transition (sonic-cfggen reads a CONFIG_DB mid-update) --
# it exits 0 either way, so exit-code checking alone will not catch this.
# Verify the file actually landed, retry once with a short wait if not.
sleep 3
config save -y
if [ ! -f /etc/sonic/config_db.json ]; then
    echo "config save -y did not produce config_db.json, retrying after 5s"
    sleep 5
    config save -y
fi
ls -la /etc/sonic/config_db.json

echo "=== ZTP Provisioning Completed ==="
date
exit 0
```

> [!WARNING]
> `config save -y` is mandatory. Without a startup `/etc/sonic/config_db.json` the engine logs `ZTP completed but startup configuration ... not found. Waiting for 300 seconds before restarting ZTP.` and re-enters discovery forever, even after `provisioning-script: SUCCESS`.
>
> **Verify the deployed file on the server matches this, don't assume.** On 2026-07-03, `config_db.json` repeatedly failed to appear despite `provisioning-script: SUCCESS` for several cycles; this was first (wrongly) diagnosed as `config save -y` silently racing against interface state. The actual cause, found by fetching `curl http://10.90.90.205:8080/ztp-provisioning.sh` directly instead of guessing: the live file on the server had `config save -y` **commented out** (`# config save -y`) from an earlier manual edit — so the command never ran at all, every single cycle. Lesson: when the deployed behavior doesn't match this doc, check the actual file being served before theorizing about race conditions or runtime failures. The `sleep`+retry logic below is a reasonable defensive measure regardless, but was not the fix for that specific incident.

> [!NOTE]
> `/var/log/ztp.log` is **not** exclusively this script's private log — SONiC's own syslog also writes the `sonic-ztp`-tagged engine/hook messages into the same file. Expect a large, mixed-content file. `grep` for `=== ZTP Provisioning` or the journal timestamp of interest rather than assuming the tail is clean. Reading it requires `sudo` (`-rw-r----- root adm`). For anything you must trust (e.g. "did config save actually work"), verify the resulting file/state directly — don't rely on this log's text. (One garbled-looking line seen 2026-07-03 turned out to be an artifact of a live terminal edit on the server, not file corruption — don't over-diagnose stray text in this log without checking.)

### 1.2 Start the HTTP server

```bash
mkdir -p ~/ztp-www
cp ztp.json ztp-provisioning.sh ~/ztp-www/
chmod 644 ~/ztp-www/ztp.json
chmod 755 ~/ztp-www/ztp-provisioning.sh
cd ~/ztp-www
nohup python3 -m http.server 8080 > /tmp/ztp_http.log 2>&1 &
```

> [!WARNING]
> Plain `nohup`, **not reboot-persistent**. After any server reboot re-run the line above, together with the usual post-reboot steps from the Kyoto report §3.2 (static route `192.168.10.0/24 via 10.90.90.11 dev Vlan2` + `systemctl restart isc-dhcp-server`).

Verify locally on the server:

```bash
curl -s http://10.90.90.205:8080/ztp.json
curl -s -o /dev/null -w "%{http_code}\n" http://10.90.90.205:8080/ztp-provisioning.sh   # 200
```

### 1.3 dhcpd option 67

`/etc/dhcp/dhcpd.conf` — add `option bootfile-name` (= DHCP option 67; SONiC's dhclient exit hook maps it to `ztp_data_url`) to the DUT-facing subnet:

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
> The URL **must be quoted**. Unquoted, `dhcpd -t` fails with `semicolon expected` at the `:`.

Apply:

```bash
sudo cp /etc/dhcp/dhcpd.conf /etc/dhcp/dhcpd.conf.bak.$(date +%s)
sudo dhcpd -t -cf /etc/dhcp/dhcpd.conf     # silence (or just the PID-file line) = OK
sudo systemctl restart isc-dhcp-server
systemctl is-active isc-dhcp-server        # active
```

---

## 2. DUT one-time prerequisite (do this BEFORE the first ZTP attempt)

Two fixes that remove entire classes of failure documented (the hard way) in §4.4. Run once per DUT; both survive `config save`.

### 2.1 Widen the route-to-server hook to cover ANY interface, not just `Vlan*`

The stock hook `/etc/dhcp/dhclient-exit-hooks.d/vlan_dhcp_server_route` (Kyoto report §5.2) only adds a host route to the ZTP/DHCP server when the bound interface name starts with `Vlan`:

```sh
if echo "$interface" | grep -q '^Vlan' && ...
```

If `Vlan951` doesn't exist yet at boot time (fresh flash, or `config_db.json` missing for any reason — see §4.4(4) for the full failure chain), ZTP legitimately falls back to treating `Ethernet11` as a routed DHCP port. The hook then never fires for it, no route to the server gets added, and the DUT's `curl` to the option-67 URL leaves via `eth0`'s default route and times out forever — even though the DHCPACK itself was perfectly healthy the whole time.

Fix once, covers both cases:

```bash
sudo sed -i "s/grep -q '\^Vlan'/grep -qE '^(Vlan|Ethernet)'/" /etc/dhcp/dhclient-exit-hooks.d/vlan_dhcp_server_route
grep -n "grep -qE" /etc/dhcp/dhclient-exit-hooks.d/vlan_dhcp_server_route   # confirm it took
```

### 2.2 Build the `Vlan951` data-path topology before relying on option 67

If this hasn't been done yet on this DUT, follow Kyoto report §5.1 in full (remove any routed IP from `Ethernet11`, `config vlan add 951`, `config vlan member add 951 -u Ethernet11`, set `AMAZON_FLAG=true` in `/usr/lib/ztp/ztp-profile.sh`, `systemctl restart ztp`). Confirm with:

```bash
show vlan brief    # Vlan951, Ethernet11 untagged member
grep -n 'AMAZON_FLAG=' /usr/lib/ztp/ztp-profile.sh   # must be true
```

Skipping this and going straight to §4.1 is exactly the condition that produces the §4.4(4) failure — §4.1 preserves whatever topology already exists but does not create it from nothing.

---

## 3. DUT-side file map (where everything lands)

All paths confirmed against sonic-ztp source (`src/sonic-ztp/.../ztp/defaults.py`, `ZTPSections.py`).

| What | Path | Lifetime |
| :--- | :--- | :--- |
| Option-67 URL as received | `/var/run/ztp/dhcp_67-ztp_data_url` | tmpfs — gone on reboot |
| Raw downloaded `ztp.json` | `/var/run/ztp/ztp_data_opt67.json` | tmpfs — gone on reboot |
| Persisted ZTP session (incl. per-section `status`, the `SUCCESS` record) | `/host/ztp/ztp_data.json` (+ `ztp_data_shadow.json`) | **persistent, survives reboot** |
| Downloaded provisioning script (the file actually executed) | `/var/lib/ztp/sections/provisioning-script/plugin` | until next ZTP session cleanup |
| Section input passed to the script as `$1` | `/var/lib/ztp/sections/provisioning-script/input.json` | until next ZTP session cleanup |
| Script's own output (mixed with engine syslog — see §1.1 note) | `/var/log/ztp.log` | persistent |

Inspect:

```bash
cat /var/run/ztp/dhcp_67-ztp_data_url                          # -> the URL
sudo cat /var/run/ztp/ztp_data_opt67.json                       # -> downloaded ztp.json verbatim
sudo ls -la /var/lib/ztp/sections/provisioning-script/
sudo cat /var/lib/ztp/sections/provisioning-script/plugin       # -> your ztp-provisioning.sh content
```

**Plugin download cache caveat** (`ZTPSections.py` ~L278: `Re-use the plugin if already present`): if `.../provisioning-script/plugin` already exists, ZTP does **not** re-download it within the same session. Across sessions this self-resolves: `__cleanup()` (ZTPSections.py L439-441) wipes `/var/lib/ztp/sections` + `/var/lib/ztp/tmp` whenever a new ZTP JSON starts at status `BOOT` — which the §4.1 re-trigger guarantees by deleting `ztp_data.json`. Manually clearing these dirs is only needed if you're editing the served script mid-session without re-triggering.

---

## 4. Re-triggering ZTP after it has reached SUCCESS

`ztp-engine.py`'s `__discover()` (~L747-753) checks candidates in strict order, and **either of the first two alone is enough to make `systemctl restart ztp` a complete no-op** — both must be cleared, not just the first one:

1. If `/host/ztp/ztp_data.json` (persistent) exists at all, the engine resumes that session. If its `status` isn't `BOOT`/`IN-PROGRESS`, it logs `ZTP already completed with result SUCCESS at ...` and exits in ~5s — no discovery, no AMAZON hook, nothing. **`FAILED` gates identically** (`ZTP already completed with result FAILED at ...`, observed 2026-07-06) — a failed cycle blocks re-triggering just as hard as a successful one.
2. **Only if that file is absent**, it checks: does `/etc/sonic/config_db.json` exist? If so (and `monitor-startup-config` is at its default `True`), it logs `Configuration file '/etc/sonic/config_db.json' detected. Shutting down ZTP service.` and exits — again with zero attempt at option-67 discovery. This is a **separate, independent gate** from (1): clearing only `ztp_data.json` walks straight into this one, since our own provisioning script's `config save -y` is what creates `config_db.json` in the first place.

Both files are on persistent storage, so both gates survive reboots.

> [!WARNING]
> Do **not** "fix" gate 2 by setting `monitor-startup-config: false` in `/host/ztp/ztp_cfg.json`. That was tried and tested 2026-07-03: it does let discovery proceed, but it does so by permanently disabling ZTP's own "don't touch a manually-configured box" safety check, and it did not by itself prevent the race in step 3 below. The correct fix is simpler and doesn't change ZTP's semantics: just remove the **file** `/etc/sonic/config_db.json`.

> [!WARNING]
> **Removing the file itself doesn't touch live config — but the ZTP startup that follows usually does** (observed 2026-07-06, correcting an earlier over-broad claim here). The `rm` leaves kernel/redis state alone. However, on ZTP startup with no `config_db.json` and no session data, `ztp-profile.sh` typically loads the ZTP discovery configuration profile via a config reload — which **wipes the running redis config (Vlan951 included) and restarts `swss`/`syncd`** (this reload is also what earlier looked like spontaneous "swss crashes" right after starting ztp). It skipped the reload on at least one run (2026-07-03) but hit on every re-trigger of 2026-07-06 — plan for it happening. The recovery is §4.1 Checkpoint 0's repair-under-running-discovery procedure: **do not restart the ztp service to fix it** (each start re-rolls the reload), rebuild the topology while discovery keeps running.

### 4.1 Surgical re-trigger (keeps live config — use this)

**Order matters.** Build/confirm the VLAN topology *before* starting `ztp`, not after. If `Ethernet11` is still a bare L3-capable port (not yet a VLAN member) when `ztp` starts scanning, ZTP's own engine can independently DHCP it as a routed port **in a race against the AMAZON hook's `Vlan951` DHCP** — and if `Ethernet11` wins, the whole ZTP cycle (including `config save -y`) completes against the wrong topology, silently destroying whatever `Vlan951` config existed before. Verified 2026-07-03: this race is real and was observed going the wrong way even with `Vlan951` nominally "present" moments earlier — the only reliable fix is ensuring `Ethernet11` is *already* a bridge slave (no independent L3 role) before `ztp` ever starts.

> [!WARNING]
> **`config vlan add` / `config vlan member add` can crash `swss`/`syncd`** (observed repeatedly 2026-07-06, including once on a freshly-rebooted DUT with no prior churn — this is not just an "accumulated churn" risk). Symptom: containers cycle (`docker ps` shows `swss`/`syncd` with an uptime far shorter than the DUT's own uptime), and both `Vlan951` and `Ethernet11` briefly report `Device does not exist` while the stack restarts. Run the two commands **one at a time**, checking `systemctl is-active swss syncd` after each, rather than batching them — if either shows anything other than `active`, stop and wait for `docker ps` to show all containers stable before continuing. This is the same underlying SAI/CPSS instability as Kyoto report §8.7, just triggered earlier in the sequence (at VLAN creation) instead of during a later bounce.

**現場短版操作**：先貼 A 段。A 段最後會做 **CP0 / Checkpoint 0**，也就是 `start ztp` 後約 30 秒的第一個檢查點。CP0 不是看最後成功沒，而是先看 `Vlan951` 有沒有被 ZTP startup reload 洗掉。

看 A 段最後輸出時，重點只有三個：

- `systemctl is-active ztp swss syncd`：`swss` / `syncd` 如果不是 `active active`，先等穩，不要急著判斷。
- `show vlan brief`：要看到 `951`，而且 port 要有 `Ethernet11`。
- `ip -br link show Vlan951`：如果出現 `Device "Vlan951" does not exist.`，代表 `Vlan951` 被洗掉，要貼 B 段修。

A 段 — 乾淨重跑一次 ZTP：

```bash
sudo systemctl stop ztp
for p in $(pgrep -x dhclient -a | grep -E "Vlan951|Ethernet11" | awk '{print $1}'); do sudo kill "$p"; done

for ip4 in $(ip -o -4 addr show dev Ethernet11 2>/dev/null | awk '{print $4}'); do sudo config interface ip remove Ethernet11 "$ip4"; done
show vlan brief | grep -q 951 || sudo config vlan add 951
sleep 2; systemctl is-active swss syncd
show vlan brief | grep -q Ethernet11 || sudo config vlan member add 951 -u Ethernet11
sleep 2; systemctl is-active swss syncd
sudo ip link set Ethernet11 up
sudo ip link set Vlan951 up
grep -q 'AMAZON_FLAG=true' /usr/lib/ztp/ztp-profile.sh || echo "WARNING: set AMAZON_FLAG=true first"

sudo rm -f /host/ztp/ztp_data.json /host/ztp/ztp_data_shadow.json /etc/sonic/config_db.json
sudo rm -rf /var/run/ztp/ztp.lock
sudo ip addr flush dev Vlan951 2>/dev/null
sudo systemctl start ztp
sleep 30
systemctl is-active ztp swss syncd
show vlan brief | grep -E "951|VLAN ID" || true
ip -br link show Ethernet11 2>&1
ip -br link show Vlan951 2>&1
pgrep -x dhclient -a | grep -E "Vlan951|Ethernet11" || true
show ztp status | head -8
```

B 段 — 只有 CP0 看到 `Vlan951` 消失時才貼。重點：**不要 restart ztp**，讓 `ztp` 繼續跑，直接在 Active Discovery 狀態下把 VLAN 修回來。這段會先殺掉 `Ethernet11` / `Vlan951` 的 dhclient，避免 `Ethernet11` 又搶到 option 67。

```bash
for p in $(pgrep -x dhclient -a | grep -E "Vlan951|Ethernet11" | awk '{print $1}'); do sudo kill "$p"; done
for ip4 in $(ip -o -4 addr show dev Ethernet11 2>/dev/null | awk '{print $4}'); do sudo config interface ip remove Ethernet11 "$ip4"; done
sudo config vlan add 951
sleep 2; systemctl is-active swss syncd
sudo config vlan member add 951 -u Ethernet11
sleep 2; systemctl is-active swss syncd
sudo ip link set Ethernet11 up
sudo ip link set Vlan951 up
sudo python3 /usr/lib/ztp/add_macentry.py
sudo python3 /usr/lib/ztp/add_StaticFDBEntry_CPU63_from_iplink.py
for p in $(pgrep -x dhclient -a | grep -E "Vlan951|Ethernet11" | awk '{print $1}'); do sudo kill "$p"; done
sudo ip addr flush dev Vlan951
sudo rm -f /var/lib/dhcp/dhclient.leases
sudo dhclient -v Vlan951 -nw
sleep 15
show ztp status | head -8
show vlan brief | grep -E "951|VLAN ID" || true
ls -la /etc/sonic/config_db.json 2>&1
```

C 段 — 暴力重測用。如果同一台 DUT 每次 A 段都會跑到 B 段，就不要等 CP0 判斷了：先清 gate/start ztp，等 ZTP startup reload 跑完，然後**不管 `Vlan951` 有沒有消失，都強制做一次 repair**，把 DHCP 來源導回 `Vlan951`。這段比 `config ztp run -y` 溫和，因為不走完整 factory reset；但它仍會刪 `/etc/sonic/config_db.json` 來重跑 ZTP。

測 provisioning script 有沒有跑到時，script 裡不要放 `sudo systemctl stop ztp`，不然 ZTP engine 可能還沒收到 section exit code 就被關掉，狀態會卡在 `provisioning-script: IN-PROGRESS`。測試用 script 建議只做 `touch /home/admin/ztp-otis.txt` 後 `exit 0`。

```bash
sudo systemctl stop ztp

for p in $(pgrep -x dhclient -a | grep -E "Vlan951|Ethernet11" | awk '{print $1}'); do sudo kill "$p"; done
sudo rm -f /host/ztp/ztp_data.json /host/ztp/ztp_data_shadow.json /etc/sonic/config_db.json
sudo rm -rf /var/run/ztp/ztp.lock
sudo ip addr flush dev Vlan951 2>/dev/null

sudo systemctl start ztp

# 等 ZTP startup reload / swss / syncd 穩定；這台常在這裡洗掉 Vlan951
for i in $(seq 1 30); do
  s="$(systemctl is-active swss syncd 2>&1 | tr '\n' ' ')"
  echo "$s"
  echo "$s" | grep -q "active active" && break
  sleep 5
done
sleep 10

# 不管 CP0 結果，直接導正：清掉 Ethernet11/Vlan951 dhclient，重建 Vlan951
for p in $(pgrep -x dhclient -a | grep -E "Vlan951|Ethernet11" | awk '{print $1}'); do sudo kill "$p"; done

for ip4 in $(ip -o -4 addr show dev Ethernet11 2>/dev/null | awk '{print $4}'); do
  sudo config interface ip remove Ethernet11 "$ip4"
done
for key in $(sonic-db-cli CONFIG_DB KEYS "INTERFACE|Ethernet11|*" 2>/dev/null); do
  ip4=${key#INTERFACE|Ethernet11|}
  sudo config interface ip remove Ethernet11 "$ip4"
done

show vlan brief | grep -q 951 || sudo config vlan add 951
sleep 2; systemctl is-active swss syncd
show vlan brief | grep -q Ethernet11 || sudo config vlan member add 951 -u Ethernet11
sleep 2; systemctl is-active swss syncd

sudo ip link set Ethernet11 up
sudo ip link set Vlan951 up
sudo python3 /usr/lib/ztp/add_macentry.py
sudo python3 /usr/lib/ztp/add_StaticFDBEntry_CPU63_from_iplink.py

for p in $(pgrep -x dhclient -a | grep -E "Vlan951|Ethernet11" | awk '{print $1}'); do sudo kill "$p"; done
sudo ip addr flush dev Vlan951
sudo rm -f /var/lib/dhcp/dhclient.leases
sudo dhclient -v Vlan951 -nw

sleep 30
show ztp status | head -12
show vlan brief | grep -E "951|VLAN ID" || true
ip -br addr show Vlan951 2>&1
ls -la /home/admin/ztp-otis.txt /etc/sonic/config_db.json 2>&1
```

**Checkpoint 1** — within ~1 min of the `DHCPACK` appearing in the journal, the URL file MUST exist:

```bash
cat /var/run/ztp/dhcp_67-ztp_data_url     # -> http://10.90.90.205:8080/ztp.json
```
Missing while the DUT has an IP -> §4.4(1) (stale lock), §4.4(2) (no option 67 in the ACK), or §4.4(5) (server-side lease desync — DUT-side all-clean but renewals silently ignored).

**Checkpoint 2** — once the URL exists, the DUT must actually be able to reach it:

```bash
ip route get 10.90.90.205    # must show the DUT's own DHCP-leased interface, NOT "dev eth0"
curl -s -m5 -o /dev/null -w "%{http_code}\n" http://10.90.90.205:8080/ztp.json   # 200
```
Wrong route or non-200 -> §4.4(3) (server/HTTP down) or §4.4(4) (routing — should not happen if §2.1 was applied).

Watch the rest:

```bash
watch -n5 'show ztp status | head -6'
sudo journalctl -u ztp -f | grep -E "Downloading|Processing|SUCCESS|FAILED"
```

Expected clean-cycle journal sequence:

```text
DHCPACK of <ip> from 192.168.10.1
Downloading provisioning data from http://10.90.90.205:8080/ztp.json to /var/run/ztp/ztp_data_opt67.json
Processing configuration section provisioning-script at ...
Processed Configuration section provisioning-script with result SUCCESS, exit code (0) at ...
ZTP successfully completed at ...
```

**Checkpoint 3 (do not skip)** — `ZTP Status: SUCCESS` does **not** guarantee `config save -y` actually landed; it can fail silently inside the provisioning script (see §1.1 warning) and the section still reports exit code 0. Confirm the file directly, and confirm the correct interface won:

```bash
show ztp status | grep Source          # must say "(Vlan951)", not "(Ethernet11)"
ls -la /etc/sonic/config_db.json       # must exist with a fresh timestamp
ip -br link show Ethernet11            # if state DOWN despite Admin/Oper up in `show interfaces status`,
                                        # run: sudo ip link set Ethernet11 up  (cheap, known variant — §5 item 3)
```
If `config_db.json` is missing, `config save -y` failed silently — run it manually (`sudo config save -y`) and confirm the file appears before considering the DUT provisioned. A reboot before this is confirmed will lose everything back to §2.2.

If the cycle ended **FAILED** instead (e.g. §4.4(4b)'s mid-cycle link drop): fix the underlying cause first, then re-run this whole procedure from step 1 — the FAILED result is persisted in `ztp_data.json` and gates every restart until removed, exactly like SUCCESS. Note `show ztp status` may keep displaying the stale FAILED (from the shadow file) even while a fresh cycle is already running — trust `journalctl -u ztp -f` for live progress, not the status display.

### 4.2 Full factory re-run (destructive — normally don't)

```bash
sudo config ztp run -y
```

Under the hood (`src/usr/bin/ztp`, `ztp_run()`) this **renames** `config_db.json` to a timestamped backup (`config_db.json.<ISO-timestamp>`) rather than deleting it outright — recoverable in principle by renaming it back, but functionally it's gone from ZTP's and the running system's perspective either way: **Vlan951/member config is wiped**, `Ethernet11` reverts to a routed discovery port, and the whole Kyoto report §5.1 DUT setup (vlan add / member add / `AMAZON_FLAG` / restart ztp) must be redone (§2.2). This is exactly the state that triggers §4.4(4) if §2.1's route-hook fix hasn't been applied. Only use this for testing the true out-of-box flow.

### 4.3 If the service refuses to start at all

Repeated restarts can trip the systemd start-limit:

```bash
sudo systemctl reset-failed ztp
sudo systemctl start ztp
```

### 4.4 Failure-mode reference (in case §2/§4.1's defenses were skipped or don't apply)

Symptom in cases (1)-(4): DUT has an IP, but `show ztp status` never leaves discovery and no `config_db.json` appears. Case (5) is different — the URL registers correctly and everything on the DUT looks fine, but ZTP still never downloads anything; the cause lives on the DHCP server, not the DUT.

**(1) Stale dhclient-hook lock.**
The dhclient exit hook (`/etc/dhcp/dhclient-exit-hooks.d/ztp`, source `src/sonic-ztp/src/usr/lib/ztp/dhcp/ztp`) writes the URL via:
```sh
take_lock dhcp && echo $new_bootfile_name > /var/run/ztp/dhcp_67-ztp_data_url
```
`take_lock` succeeds only if `/var/run/ztp/ztp.lock` is absent **or** already owned by the same `proto:interface`. A dhclient killed mid-hook leaves the lock owned by e.g. `dhcp:eth0` — every later `dhcp:Vlan951` hook invocation then fails `take_lock` **silently** (no log line at all) and the URL file is never written. §4.1 now clears this unconditionally on every re-trigger; this case should only occur if you bypassed §4.1's procedure.
```bash
sudo cat /var/run/ztp/ztp.lock/interface 2>/dev/null   # owned by someone else = confirmed
cat /var/run/ztp/dhcp_67-ztp_data_url 2>/dev/null || echo URL_FILE_MISSING
```
Fix:
```bash
sudo rm -rf /var/run/ztp/ztp.lock
sudo pkill -f "dhclient.*Vlan951"
sudo ip addr flush dev Vlan951
sudo dhclient -v Vlan951 -nw
sleep 5
cat /var/run/ztp/dhcp_67-ztp_data_url    # must now contain the URL
```

**(2) The ACK genuinely lacks option 67** — server-side regression:
```bash
# NOTE: the AMAZON hook starts the Vlan951 dhclient with no -lf flag
# (`dhclient -v Vlan951`), so its lease lands in the plain DEFAULT file, not a
# per-interface one. `dhclient.Vlan951.leases` will be empty/absent even when
# everything is working — check the default file instead:
sudo cat /var/lib/dhcp/dhclient.leases 2>/dev/null | grep -A2 bootfile-name
grep bootfile-name /etc/dhcp/dhcpd.conf      # on .161
systemctl is-active isc-dhcp-server          # on .161
```

**(3) URL registered but download fails** — HTTP server on .161 died (plain `nohup`, dies on server reboot):
```bash
sudo journalctl -u ztp --no-pager | grep -iE "Failed to download|malformed"
curl -s -m5 http://10.90.90.205:8080/ztp.json | head -3     # from the DUT
# if dead, on .161:  cd ~/ztp-www && nohup python3 -m http.server 8080 > /tmp/ztp_http.log 2>&1 &
```

**(4) URL registered, HTTP server alive, but `curl` times out (`Failed to download ... returncode=20`)** — the DUT's route to the server is wrong. This is the failure §2.1 prevents at the root; reaching this point means that fix wasn't applied (or was reverted, e.g. by an image/config that overwrote the hook file). Root cause: on a boot with no `Vlan951` yet, ZTP grabs `Ethernet11` as a routed DHCP port, the un-widened route hook only matches `Vlan*` and never fires for it, so `eth0`'s default route wins for traffic to the server.
```bash
ip route get 10.90.90.205                    # shows "via 192.168.80.254 dev eth0" instead of via the DHCP interface
ip -br addr | grep -E "Ethernet11|Vlan951"   # confirms which interface actually holds the 192.168.10.x lease
```
Fix immediately, then apply §2.1 so it can't recur:
```bash
sudo ip route replace 10.90.90.205/32 via 192.168.10.1 dev Ethernet11   # or dev Vlan951, per the check above
# ZTP's curl retries on its own — no restart needed, it proceeds once the route exists
sudo sed -i "s/grep -q '\^Vlan'/grep -qE '^(Vlan|Ethernet)'/" /etc/dhcp/dhclient-exit-hooks.d/vlan_dhcp_server_route
```
After ZTP completes, rebuild the `Vlan951`/`Ethernet11` topology per §2.2 (this cycle's `config save -y` persisted the routed-`Ethernet11` layout, not the VLAN one) and run `config save -y` again with the corrected topology.

**(4b) ztp.json downloads fine, but the cycle ends `FAILED` with `Failed to download plugin ... provisioning-script`** — the link died *mid-cycle*, between the two downloads. Observed 2026-07-06: `Ethernet11` dropped to kernel-`state DOWN` (§5 pitfall 3) after `ztp.json` was fetched but before the plugin fetch; curl error 28 after its retries, section marked FAILED, and **FAILED persists in `ztp_data.json` and gates all further restarts** just like SUCCESS does. Fix: `sudo ip link set Ethernet11 up`, verify `curl` works from the DUT, then re-trigger per §4.1 (the FAILED session data must be removed like any other).

**(5) DUT has a valid IP, option-67 URL was correctly written at BOUND, but ZTP still never downloads anything** — the server-side lease database has desynced from what the DUT believes it holds. Discovered 2026-07-06, root cause found by checking `.161`'s own syslog (not the DUT) instead of assuming a DUT/network problem. Mechanism:
1. ZTP's own periodic `Restarting network discovery.` cycle wipes `/var/run/ztp/` (including the just-written option-67 URL file) via internal cleanup, but does **not** force a fresh DHCP cycle — the DUT just waits for the dhclient's own natural renewal timer, which can be minutes away.
2. When that unicast RENEW finally fires, if `.161`'s `dhcpd.leases` no longer has a matching record for that IP (easily caused by rapid DUT reboot/lease churn — repeated `DHCPRELEASE`/re-lease cycles can desync the server's lease file), the server **silently ignores** the renewal — no NAK, no ACK, nothing.
3. The DUT is left retrying an unwinnable unicast RENEW forever, with a perfectly valid-looking IP and nothing in the DUT-side logs to suggest why nothing progresses.

Diagnose from the **server** (`.161`), not the DUT:
```bash
# on .161:
sudo grep -iE "$IP_IN_QUESTION" /var/log/syslog | tail -15
# look for: "unknown lease", "Abandoning IP address ... pinged before offer",
# or "DHCPRELEASE ... (not found)" -- any of these confirm desync
```
Fix on the DUT — force a fresh broadcast DISCOVER instead of waiting for/retrying the doomed RENEW:
```bash
sudo pkill -f "dhclient -v Vlan951"
sudo ip addr flush dev Vlan951
sudo rm -f /var/lib/dhcp/dhclient.leases    # see (2)'s note on why this is the real lease file
sudo dhclient -v Vlan951 -nw
sleep 8
cat /var/run/ztp/dhcp_67-ztp_data_url        # should now be populated; DUT will get a *different* IP than before
```
This is unrelated to VLAN topology, routing, or the AMAZON hook — everything on the DUT can be configured perfectly and this will still hang. If cases (1)-(4) are all clean but ZTP still isn't progressing, check the server's syslog before re-diagnosing the DUT further.

---

## 5. Known operational pitfalls (cross-references)

1. **Recovery trap after SUCCESS**: once ZTP has a persisted SUCCESS, the Kyoto report §8.7 freeze-recovery pair (`restart swss` + `restart ztp`) breaks — `restart ztp` no longer re-runs the AMAZON hook, so the CPU63 VLAN entry wiped by `restart swss` never comes back. Re-run the hook scripts directly instead (`python3 /usr/lib/ztp/add_macentry.py && python3 /usr/lib/ztp/add_StaticFDBEntry_CPU63_from_iplink.py`), then restart the Vlan951 dhclient. Full decision tree: Kyoto report §8.7.1.
2. **Repeated ZTP retries = VLAN churn = RX-freeze risk**: every failed cycle re-bounces `interfaces-config`/ports; each bounce is a probabilistic shot at the Ethernet11 RX freeze (Kyoto report §8.7.1: froze after 2 bounces once, survived 20 bounces another time). Get the `ztp.json` schema right *before* pointing the DUT at it to keep the cycle count at 1.
3. **Mild variant after multiple cycles**: `Ethernet11` can come up kernel-`state DOWN` while `show interfaces status` says up/up. Check `ip -br link show Ethernet11` and fix with `sudo ip link set Ethernet11 up` before escalating to the full recovery.
4. **CPSS health check** (read-only, LuaCLI on `localhost:12345` inside the DUT): healthy state shows CPU port 63 as tagged member of VLAN 951 —
   ```bash
   # /tmp/cpss_check_vlan951.py — see Kyoto report §8.7.2 for the full script
   python3 /tmp/cpss_check_vlan951.py     # expect: 951  0/63 tagged / 0/10 untagged
   ```
5. **Stale `/var/run/ztp/ztp.lock` blocks option-67 registration silently**: fixed at the root by making §4.1's re-trigger clear it unconditionally every time. See §4.4(1) if it's still hit.
6. **Route hook only matching `Vlan*` blocks option-67 download silently**: fixed at the root by §2.1. See §4.4(4) if it's still hit (most likely cause: §2.1 was never applied on this DUT, or a re-image/factory reset overwrote the hook file and it needs re-applying).
7. **`config_db.json`'s mere presence blocks discovery, independent of the `ztp_data.json` SUCCESS check**: `ztp-engine.py`'s `__discover()` refuses to even attempt option-67 discovery if `/etc/sonic/config_db.json` exists (`MANUAL_CONFIG` mode, "Configuration file detected. Shutting down ZTP service."). Since this SOP's own provisioning script creates that file via `config save -y`, **every successful cycle blocks the next re-trigger by design** unless the file is removed first. §4.1 now removes it as a required step (not the `monitor-startup-config` override — see the warning in §4, that path was tried and is wrong).
8. **`Ethernet11` can win a DHCP race against `Vlan951` and destroy the VLAN topology**: if `Ethernet11` isn't already a VLAN member when `ztp` starts, ZTP's engine can independently DHCP it as a routed port faster than the AMAZON hook gets to `Vlan951` — and if it does, that cycle's `config save -y` persists the routed-`Ethernet11` layout, wiping `Vlan951`. Confirmed 2026-07-03 (`ZTP Source: dhcp-opt67 (Ethernet11)`) even with `Vlan951` nominally present moments before. Fix: build the VLAN topology and confirm it (§2.2) **before** starting `ztp`, per §4.1's ordering.
9. **Always verify the file actually being served, don't diagnose from behavior alone**: `provisioning-script: SUCCESS` does not guarantee `config save -y` ran — the live `.161` file was found to have it commented out entirely (see §1.1 warning), from an earlier manual edit, not a runtime race. When results don't match this doc, `curl http://10.90.90.205:8080/ztp-provisioning.sh` and diff it against §1.1 before theorizing. The §1.1 script's sleep+retry is still kept as a reasonable defensive measure, but confirm the file directly (§4.1 Checkpoint 3) rather than trusting `provisioning-script: SUCCESS` alone.
10. **`/var/log/ztp.log` is a shared file, not the script's private log**: SONiC's own syslog also writes `sonic-ztp`-tagged messages into it. Unexpected text in this log can also come from the *deployed script itself* having been hand-edited (as in pitfall 9) — check the served file before assuming the log is corrupted or misleading.
11. **`config vlan add`/`config vlan member add` can crash `swss`/`syncd`**: observed 3 times on 2026-07-06, including on a freshly-rebooted DUT with zero prior churn this session — this is not purely a function of accumulated operations. Run these two commands one at a time and check `systemctl is-active swss syncd` between them (now baked into §4.1 step 2); if either isn't `active`, wait for `docker ps` to show all containers stable before proceeding. No config-level fix exists; this is the same SAI/CPSS instability as Kyoto report §8.7, just surfacing earlier in the sequence.
12. **DHCP server-side lease desync can hang ZTP indefinitely with no DUT-side symptom**: found 2026-07-06 by checking `.161`'s syslog instead of the DUT. If the server's `dhcpd.leases` loses track of an IP the DUT still believes is bound (easily caused by rapid DUT reboot/re-lease cycles), unicast RENEW requests are silently ignored forever — no NAK, no ACK. Everything on the DUT (route, VLAN, hook, URL file) can be perfectly correct and this will still hang. Full diagnosis + fix: §4.4(5). Rule of thumb: if DUT-side checks all pass but nothing progresses, check the server's syslog next, not the DUT again.
13. **The dhclient started by the AMAZON hook uses the default lease file, not a per-interface one**: `dhclient -v Vlan951` (no `-lf` flag) writes to `/var/lib/dhcp/dhclient.leases`, not `dhclient.Vlan951.leases`. Checking the per-interface-named file (which doesn't exist for this specific client) gives a false "no lease" reading — see §4.4(2)'s corrected command.
