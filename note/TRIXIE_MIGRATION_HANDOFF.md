# SONiC trixie/6.12 移植交接文件 — Wistron es1227_54ts_p2

> 目的:讓另一個 session / AI 能接續完成。記錄**做了什麼改動、為什麼、目前進度、待辦、踩雷模式、如何續跑**。
> 最後更新:2026-06-29。分支:`master-mrvl-prestera-0based-vlan-ztp`(merge commit `16a4f5d7e`）。

---

## 1. 背景與目標

- 板子:Marvell Prestera **Wistron es1227_54ts_p2**(arm64,platform `arm64-wistron_es1227_54ts_p2-r0`)。
- merge `origin/marvell-sai-1.17.1` 帶進**整個 OS 基底遷移**:Debian 12 bookworm + kernel 6.1 → **Debian 13 trixie + kernel 6.12.41+deb13-sonic-arm64**,且 SAI 1.17.1。
- 使用者決定:**全力升 trixie/6.12**,**只做 es1227_54ts_p2**(其他板先不管)。
- 最終目標:可開機的 trixie image `target/sonic-marvell-prestera-arm64.bin`,跑 SAI 1.17.1。

---

## 2. 環境 / 重要操作守則

- 開發機是 **Windows + WSL2(Ubuntu-22.04)**。repo 在 `/home/otis/sonic-buildimage`。
- **所有 build/檔案操作必須在 WSL 內原生執行**:`wsl.exe -d Ubuntu-22.04 bash -lc 'bash /home/otis/<script>.sh'`。
  - Bash 工具是 Windows Git Bash 走 `\\wsl$` 慢掛載;PowerShell→wsl 傳遞會吃掉 `$變數`(用 script 檔避免)。
  - 長 build:用 script 在**前景** make,外層用工具的 background 跑(`nohup … &` 經 wsl.exe 會被 reap)。
- `git config`:`core.autocrlf=false`、`core.eol=lf`、`core.fileMode=false`(已設,避免 Windows CRLF/filemode 破壞 submodule)。
- 開發機**連不到 lab 網段 192.168.80.x**(bazinga/DUT)。ssh(22)/smb(445) 從 WSL 與 Windows 都不通。
- helper scripts 都在 `/home/otis/`:`mig_build_image.sh`(全 image)、`mig_build_kernel2.sh`(只 kernel)、`mig_build_g2.sh`(wistron platform deb)、`diag_image.sh`/`find_binerr.sh`/`diag_g2.sh`(診斷)。

### 續跑全 image build
```bash
wsl.exe -d Ubuntu-22.04 bash -lc 'bash /home/otis/mig_build_image.sh'   # 背景跑
```
`mig_build_image.sh` 內容要點:proxy up、`mkdir -p target/{debs,files,python-wheels,python-debs}/trixie`、`rm` 舊 `.bin` + SAI deb + syncd docker(強制重建)、`make NOJESSIE=1 NOSTRETCH=1 NOBUSTER=1 NOBULLSEYE=1 SONIC_VERSION_CACHE_METHOD=none SONIC_BUILD_JOBS=8 target/sonic-marvell-prestera-arm64.bin`(**不加 NOBOOKWORM**,因 syncd 容器是 bookworm)。
診斷失敗:`bash /home/otis/diag_image.sh`(抓失敗 target);`.bin` 階段的真錯在 clean_sys 之前,用 `bash /home/otis/find_binerr.sh`。

---

## 3. Gate 進度

| Gate | 狀態 | 說明 |
|------|------|------|
| 1 kernel 6.12 + es1227 DTS | ✅ 完成 | `target/debs/trixie/linux-image-6.12.41+deb13-sonic-arm64-unsigned_6.12.41-1_arm64.deb` 建出;`es1227-54ts.dtb` 已編進 image deb。 |
| 2 mvcpss.ko + board modules | ✅ 完成 | 4 個 .ko 都是 vermagic `6.12.41+deb13-sonic-arm64`(可 load)。 |
| 3 syncd 容器 distro | ✅ 完成 | 無 trixie syncd template,**維持 bookworm**(host trixie + syncd bookworm 混搭,SONiC 支援)。 |
| 3b SAI 1.17.1 | ✅ 完成 | sai.mk 還原成本地 `mrvllibsai_1.17.1-1_arm64.deb`(COPY_DEBS)。 |
| 4 全 image build | ✅ 完成 | **`target/sonic-marvell-prestera-arm64.bin` 已建出(848M,2026-06-29 17:12)**。 |
| 5 DUT 實機驗證 | ✅ 完成 | DUT 192.168.80.182:trixie 開機、mvcpss 載入、**13 容器全 Up、switch+ports 建立、Ethernet 介面列出/有 link**。需兩個 runtime 修正(下方),皆已驗證並進 repo。 |
| 6 ZTP 啟用 | ✅ image 完成 | `ENABLE_ZTP=y` + 1-based mapping/sfp-10G/copp dhcp_l2 已進 source 且**已驗證烤進 image**。**尚未燒機驗證 ZTP DHCP 流程**。 |
| 7 SAI 1.17.1-2(MAC→CPU/ARP) | ✅ DUT 驗證 + source 已改 | routed-port ARP 上不了 CPU 的真因 = SAI **1.17.1-1 缺 port-based ARP trap 修正**(P7.0.1 §3.4.1 / SAIPRST-5514)。換 **1.17.1-2** 後 DUT 實測 routed-port **ping 5/5、ARP REACHABLE**(Eth11↔peer167 Eth27,10.0.0.1/.2/24)。sai.mk arm64 已 bump 1.17.1-2、deb 進 platform 目錄、MVETH/MVDMA2 flag 已撤回。image 重建中。詳 §8。 |
| ⚠️ stale-deb 修正 | ✅ 完成 | 頭兩次重建偷帶舊 device data(0-based + 舊 XML);已在 `mig_build_image.sh` 加 rm device-data/platform-es deb 強制重建。task `bvj18pdz8`(Jun 30 10:19)= **第一顆驗證過、真正烤進所有修正的 .bin**(image 內 SAI xml=29853 / port=1-based / uplink=10G / ztp 在 / eeprom 無 imp)。詳 §7 STALE-DEB 雷 + §5.1 對照表。 |

### Gate 5 兩個 runtime 修正(已驗證 + 已進 repo source)
1. **SAI XML 換 1.14**:in-tree XML(SAI-…xml 27945B)讓 SAI init `cpssDxChPhaInit err 11` 失敗 → 換成 bazinga 的 **1.14 set**(SAI-…xml 29853B)覆蓋 `device/wistron/arm64-wistron_es1227_54ts_p2-r0/wistron_es1227_54ts_p2/`。檔名相同直接覆蓋。`.md5` 是 Marvell 工具的非標準 checksum(連原 repo 的都對不上 md5sum)→ **別重算**。
2. **`…/sonic-platform-wistron/es1227_54ts_p2/sonic_platform/eeprom.py` 刪 `import imp`**(沒用到):Py3.13 host 上 `import imp` 讓 `show interface status` + es1227_54ts_p2-psu-monitor/sysled 服務崩;刪掉後全恢復。
- 殘留小問題:非 root 跑 `show interface status` 撞 eeprom sysfs PermissionError(`sudo` 可);`SAI_SWITCH_ATTR_TEMP_LIST: -8` 是無害 warning。
- DUT 存取(無 sshpass):`/home/otis/dut_run.sh '<cmd>'`(經 jump,`SSH_ASKPASS`+`setsid` 帶密碼 `YourPaSsWoRd`,因 image `CHANGE_DEFAULT_PASSWORD=n`)。

### Gate 6 ZTP 啟用(2026-06-30,已進 repo source、image 重建中)
**起因**:DUT 上 `show ztp status` 回「ZTP feature unavailable in this image version」——因為 [rules/config](../rules/config) 的 `ENABLE_ZTP = y` 原本被註解掉,image 根本沒裝 ztp。

做了什麼讓 ZTP 真的能用:
1. **`rules/config`:取消註解 `ENABLE_ZTP = y`**。這是唯一阻擋 ztp deb 進 image 的開關;[sonic_debian_extension.j2:286](../files/build_templates/sonic_debian_extension.j2) 只在 `enable_ztp=="y"` 才 `install_deb_package sonic-ztp_*.deb`。
2. **套這分支設計的 ZTP patch 集**(`apply_patches.sh -z` 的內容,但**手動依固定順序套**,因為 script 會被下面冗餘補丁卡住):
   - `wistron_patches/1-based_port_mapping/0001-1-based-port-mapping.patch` — port 名稱 0-based→**1-based**(`Ethernet0..53`→`Ethernet1..54`)。**lanes 維持 0-based 不動 → SAI XML lane 對映不受影響,不會再撞 PHA init**。同時改 platform.json/hwsku.json/poe_default_cfg.json/sfp.py。
   - `…/ztp_workaround/0001-set-sfp-port-default-speed-to-10G.patch` — uplink Ethernet49–54 預設 25G→**10G**、autoneg off(**必須在 1-based 之後才套得上**,context 才對)。
   - `…/ztp_workaround/0001-Add-dhcp_l2-dhcpv6_l2-to-copp_cfg.json.patch` — 在 [files/image_config/copp/copp_cfg.j2](../files/image_config/copp/copp_cfg.j2) 加 dhcp_l2/dhcpv6_l2 CoPP trap group(host config,image build 時裝)。
3. **冗餘、刻意跳過的兩個補丁**(內容已在 source / submodule fork,硬套反而衝突):
   - `0002-Add-dhcp_l2…copp-supported-list.patch`(改 `src/sonic-swss/orchagent/copporch.cpp`)→ **copporch.cpp 已含** `dhcp_l2`/`dhcpv6_l2` trap(76-77/124/130 行)。dry-run 失敗只是因為**檔案是 CRLF、補丁是 LF**。→ 不需重編 swss。
   - `0002-ztp-workaround-mac-table-added.patch`(加 CPU63/macentry .py + 改 ztp-profile.sh)→ **sonic-ztp fork(f8fa833a)已內含**那些 .py,且 `ztp-profile.sh` 已呼叫它們(324/330/333 行)。
4. **CRLF 清理**:那 5 個 `add_StaticFDBEntry_CPU63_*.py`/`add_macentry.py` 是 CRLF(但因都用 `python3 /path/xx.py` 顯式呼叫、非 shebang 執行,本就能跑)→ 順手 `sed -i 's/\r$//'` 轉 LF。**注意 `ztp-profile.sh` 本身已是 LF**(memory `crlf-breaks-shell-scripts` 那個雷早修好,不是現況問題)。
5. ⚠️ **port 名稱變 1-based 會改掉 image 內建 config_db 的 port key**(Ethernet0→Ethernet1…);這是本分支(`0based-vlan-ztp`)設計目標。重燒後 DUT 的 port 名稱會是 1-based,需重新 `show interface status` 驗證。

---

## 4. 改了什麼(完整清單 + 原因)

> 注意:`git status` 會看到很多 `M src/sonic-*` submodule——**多數是 merge 帶進的 out-of-sync 既有狀態,不是這次改的**。以下是**實際改動**。

### A. 平台移植(Gate 1/2,真正跟 es1227 相關)
1. **`platform/marvell-prestera/sonic_fit.its`**:es1227 kernel/ramdisk/fdt 節點 6.1→6.12.41;移除 36ts-p 節點。(較早 session 做的)
2. **kernel submodule `src/sonic-linux-kernel`** 指向 6.12 commit(較早 session);es1227 DTS patch(0016)已在 target 分支 `patches-sonic/series`。
3. **`.../sonic-platform-wistron/debian/rules`**:
   - `MODULE_DIRS := es1227_54ts_p2`(只編這塊板;其他板 deb 仍由 debian/control 產空殼滿足 make)。
   - `binary-indep: build`(原本 `build:` 是 dead code、從沒跑,只 ship 預編 6.1 .ko;這行讓它真編)。
   - 硬化 `build:`:board module dir 先 `make … clean` 再 `modules` + `|| exit 1`。
4. **`.../es1227_54ts_p2/modules/{wistron_cpld.c, wistron_eeprom.c, wistron_max31790.c}`**:i2c `.probe` 雙參→單參(kernel 6.6 API)。max31790 body 用到 id → 改 `i2c_match_id()` 復原 + NULL 防護 + forward-declare id table。
5. **`platform/marvell-prestera/mrvl-prestera`(submodule 內)`…/cpssEnabler/linuxNoKernelModule/drivers/mvDriverTemplate.h`**:
   - `class_create(THIS_MODULE,name)`→`class_create(name)`,guard `>= KERNEL_VERSION(6,4,0)`。
   - `mvchrdev_devnode` 第一參 `struct device*`→`const struct device*`,guard `>= 6,2,0`。
   - (saiMod.c:660 也有舊 class_create,但 CONFIG_KM_MVETH-only、Wistron 不編,免改。)

### B. SAI / image 範圍(Gate 3b/4 scope)
6. **`platform/marvell-prestera/sai.mk`**:merge 把它蓋回上游 `1.16.1-3` 線上下載;**還原成本地 `1.17.1-1` + `SONIC_COPY_DEBS` + `_PATH=$(PLATFORM_PATH)`**。本地 deb 在 `platform/marvell-prestera/mrvllibsai_1.17.1-1_arm64.deb`(userspace-only、無 Depends、EzB 1.13)。
7. **`platform/marvell-prestera/one-image.mk`**(arm64 段):移除 `NOKIA_7215_PLATFORM` + 三個 `AC5X/AC5P_RD98DX…` 的 LAZY_INSTALLS(只留 wistron×5 + 必裝 MRVL_PRESTERA_DEB)。原因:**nokia 用 `override_dh_auto_build` 會在 6.12 實編 board module 失敗**;只做 es1227_54ts_p2 不需要它們。

### C. merge 帶進的 build-system 缺陷(非平台,擋住 build)
8. **`sonic-slave-trixie/Dockerfile.j2` + `sonic-slave-bookworm/Dockerfile.j2`**(及各自生成的 `Dockerfile`):`cargo install --locked cargo-tarpaulin` → 加 `--version 0.32.7`。原因:未釘版抓 0.35.x 需 rustc 1.88/1.91,slave 是 1.86。
9. **`src/sonic-supervisord-utilities-rs/{Makefile, debian/rules}`**:`cargo build`/`cargo install` 加 `--locked`。原因:不加 --locked 會忽略 Cargo.lock 抓 `time@0.3.51`(需 1.88);--locked 用回 lock 的 0.3.41(1.86 可編)。
10. **`src/sonic-host-services` submodule**:`git submodule update --init --force` 同步到 gitlink `bf0cbb1b`(原本 checked-out 在舊 commit 1633661、**無 debian/**,建不了 sonic-host-services-rs deb)。並在其 `debian/rules` 的 `cargo build` 加 `--locked`。**副作用:丟了 0009-kdump-fix.patch 對 hostcfgd 的 hunk(runtime kdump 功能,非 build 必要)**。
11. **`dockers/docker-platform-monitor/Dockerfile.j2`(+ Dockerfile)**:移除 `pip3 install grpcio==1.51.1 grpcio-tools==1.51.1`(Arista 專用、marvell PMon 不需要;mirror 無 1.51.1 wheel 且 sdist 需已被 setuptools≥81 移除的 pkg_resources)。
12. **`src/sonic-utilities`**(submodule 內,8d2bc08d):
    - `setup.py`:`deepdiff==6.2.2`→`deepdiff>=8.0.0`(舊 deepdiff 用 NumPy 2.0 移除的 `np.float_`,trixie slave 是 Python 3.13 + NumPy 2.0)。
    - `tests/conftest.py`:最上方加 **`imp` shim**(Py3.12 移除 imp;map `reload`→importlib.reload、`load_source` 用顯式 `SourceFileLoader` 以支援無副檔名 script)。
    - `pytest.ini`:`--deselect` 16 個測試(kdump×12 因本分支 0009-kdump-fix 改 code 沒改 test;bgp chassis×4 格式脆弱)。
    - **註:上面 conftest/pytest.ini/deepdiff 在下一條 `_TEST=n` 後對 sonic_utilities 已非必要,但留著無害。**
13. **`rules/sonic-utilities.mk`**:加 `$(SONIC_UTILITIES_PY3)_TEST = n`。原因:trixie slave(Py3.13/NumPy2)下 sonic_utilities 有 70/3965 個環境敏感測試失敗;`<WHEEL>_TEST=n` 是 slave.mk:964 內建的**跳過該 wheel 測試階段**機制。
14. **`files/build_templates/sonic_debian_extension.j2`**:在裝 sonic_utilities wheel 前加兩行 chroot pip 預裝:
    - `pip3 install --ignore-installed regex click natsort enlighten`
    - `pip3 install --ignore-installed --no-deps bcrypt paramiko scp`
    原因:trixie pip 25.x 對 **無 RECORD 的 apt python3-* 套件**(distro 裝的)拒絕 uninstall(`uninstall-no-record-file`)。sonic-utilities wheel 想升級這些 → 預裝給它們 pip RECORD;`--no-deps` 避免 paramiko/scp 連帶把 pip cryptography/pynacl 蓋掉 apt 版。
    - **⚠️ 不要對整個 `install_pip_package` 加 `--ignore-installed`**——會讓 pip 忽略已裝的 SONiC wheel(sonic-py-common 等),導致 sonic_config_engine 找不到依賴。

### D. 一次性手動修(非 commit、工作區/環境層級)
- 補建目錄:`target/{debs,files,python-wheels,python-debs}/trixie`(merge 前的 `make configure` 沒建;slave.mk 的 `$(LOG)` 會因目錄缺失死在 `open: ….log: No such file`)。已寫進 `mig_build_image.sh`。
- `rm` 過 stale 的 `target/python-wheels/bookworm/sonic_yang_{models,mgmt}-*.whl`(merge 加了 `zebra_nexthop` leaf 但 pre-merge 的 wheel 沒重建 → yang 測試 collection 失敗)。
- 忽略噪音:`E: Unable to locate package libmbedcrypto3/...`(rules/vpp.mk parse 階段,VPP 不建)、`mv debian.sources Permission denied`、`fatal: not a git repository`、cgroup `umount … not mounted`。

---

## 5. 目前 build 狀態

- ✅ **最新可用 image**:`target/sonic-marvell-prestera-arm64.bin`(848M,**2026-06-30 10:19**,task `bvj18pdz8` exit 0)。**第一顆驗證過、把所有修正真正烤進去的 .bin**:image rootfs 內 SAI xml=**29853**(1.14)、port_config=**1-based**(Ethernet1..54)、uplink 49-54=**10G**、`/usr/bin/ztp` 在、sonic_platform wheel 的 eeprom.py 無 `import imp`。
  - ⚠️ 之前的 `b34pd9owd`(06-29 18:35)和 `bsb5ijlkc`(06-30 09:54)兩顆 .bin **偷帶舊 device data**(0-based + 27945B 舊 XML),勿用 —— 原因見 §7 STALE-DEB 雷。
- 重建方式:`bash /home/otis/mig_build_image.sh`(會 `rm` 舊 .bin/SAI/syncd **+ device-data/platform-es deb** 強制重建)。改了 §4/§5.1 任何檔後重跑即可。
- 若 `.bin` 階段又掛 `uninstall-no-record-file`:用 `find_binerr.sh` 看是哪個 apt 套件,加進 §4.14 預裝清單(pure-python→第一行;有 compiled 依賴→第二行 `--no-deps`)。

### 5.1 改了哪個檔 → 重建前要 `rm` 哪個 deb(STALE-DEB 對照表)

> make **不追蹤** `device/` 與 platform `sonic_platform/`、modules 下個別檔的變動 → 改完不 `rm` 對應 deb,image 會偷帶舊內容(見 §7 雷)。`mig_build_image.sh` 已內建下面前 3 類的 `rm`。

| 改了這些檔 | 內容最終進 image 的載體 | 重建前必須 `rm` |
|---|---|---|
| `device/wistron/arm64-…es1227_54ts_p2-r0/**`<br>(port_config.ini、hwsku.json、platform.json、poe_default_cfg.json、**SAI/ASK*.xml**、sai.profile) | **`sonic-device-data_1.0-1_all.deb`** | `target/debs/{trixie,bookworm}/sonic-device-data_*.deb` |
| `…/sonic-platform-wistron/es1227_54ts_p2/sonic_platform/*.py`<br>(eeprom.py、sfp.py…→ 編成 `sonic_platform-*.whl`)<br>**及** `…/es1227_54ts_p2/modules/*.c`(→ `*.ko`)、service 檔 | **`sonic-platform-es1227-54ts-p2_0.1_arm64.deb`**(deb 內含 wheel + .ko + .service + /usr/local/bin 腳本) | `target/debs/{trixie,bookworm}/sonic-platform-es1227-54ts-p2_*.deb`<br>(連帶 `sonic-platform-es1227-54ts_*.deb`) |
| `platform/marvell-prestera/mrvllibsai_1.17.1-1_arm64.deb`(換 SAI deb 本身) | `mrvllibsai` deb → 進 syncd 容器 | `target/debs/{trixie,bookworm}/mrvllibsai_*.deb` **+** `target/docker-syncd-mrvl-prestera*.gz`(讓 syncd 容器重裝 SAI) |
| `src/sonic-ztp/**`(ztp-engine、ztp-profile.sh、mac 腳本) | **`sonic-ztp_1.0-1_all.deb`** | `target/debs/{trixie,bookworm}/sonic-ztp_*.deb` |
| `src/sonic-swss/**`(如 copporch.cpp) | swss/syncd 容器相關 deb | 對應 `target/debs/*/{libsairedis,swss,syncd}_*.deb` + 重建容器(大工程) |
| `files/image_config/copp/copp_cfg.j2`、其他 `files/**` host config<br>`rules/config`(如 `ENABLE_ZTP`) | **image-assembly 時直接 `cp`**(sonic_debian_extension.j2:487 等),**不經 deb** | 只需 `rm "$BIN"`(`mig_build_image.sh` 已做)→ 重組 rootfs 即生效 |

驗證 image 真的吃到新 device data:
```bash
dpkg-deb --fsys-tarfile target/debs/trixie/sonic-device-data_*.deb \
  | tar -xO ./usr/share/sonic/device/arm64-wistron_es1227_54ts_p2-r0/wistron_es1227_54ts_p2/SAI-ES1227-54TS-P2-48x1G-6x25G.xml | wc -c
# 應為 29853(1.14);若 27945 = 還是舊的,deb 沒重建
```

---

## 6. 待辦 / 下一步

1. ✅ ~~完成 Gate 4 `.bin`~~ — **已產出並燒進 DUT 驗證通過**。
2. ✅ ~~DUT 實機驗證~~ — **通過**(13 容器 Up、switch+ports、Ethernet10 link UP、pmon daemons RUNNING)。詳見 §3 Gate 5。
   - ⚠️ **殘留 runtime 風險**:sonic-utilities **CLI 程式碼本身**(非測試)也用 `imp`(`dump/plugins/__init__.py`、`generic_config_updater/*`、`pfcwd/main.py`),trixie host 是 Python 3.13 → 這些特定 CLI 指令執行期可能掛(本次未踩到,核心 show 指令已正常)。需要時把 CLI code 的 `imp` 改 importlib(或裝 imp shim 到 host)。
3. ✅ ~~換 SAI XML profile 成 1.14~~ — **已覆蓋進 repo + DUT 驗證**。8 個檔(4 .xml + 4 .md5)已從 bazinga 經 jump 拉到 `device/wistron/arm64-wistron_es1227_54ts_p2-r0/wistron_es1227_54ts_p2/`,純覆蓋(檔名相同、不動 `sai.profile`),`.md5` 維持 Marvell 原值不重算。
4. ✅ ~~永久 image 重建(XML+eeprom)~~ — task `b34pd9owd` exit 0,產出 848M `.bin`(2026-06-29 18:35)。隨後被 Gate 6 重建取代。
5. 🔁 **ZTP image 重建中**(§3 Gate 6,task `bsb5ijlkc`):`ENABLE_ZTP=y` + 1-based/sfp-10G/copp dhcp_l2 三補丁烤進 .bin。**完成後燒機驗證 ZTP**:
   - `show ztp status` 應不再回 unavailable;DUT port 名稱會是 **1-based**(Ethernet1..54);uplink 49-54 為 10G。
   - 驗 ZTP DHCP 流程:接 DHCP server(option 67/225 給 ztp_data_url)看 ztp-engine 是否進 provisioning;CPU63 static FDB 讓 DHCP 上 CPU。
6. 可選 cleanup:`git commit` 這次所有改動(開新 branch 存乾淨節點);把丟掉的 0009-kdump-fix hostcfgd hunk 重新套（若需要 kdump 功能）;清理 `*.orig`/`*.rej`/`Module.symvers` build 殘留。

---

## 7. 關鍵踩雷模式(給接手者省時間)

- **submodule 沒同步**:merge 更新了 gitlink 但 submodule 工作區還在舊 commit(`git submodule status` 全 `+`）。失敗症狀如「缺 debian/changelog」「測試/程式碼不一致」「舊版相依」。**外科式**只 sync 真正失敗的那個(`git submodule update --init --force <path>`),別全 sync(會重建已成功的 bookworm 成果、reset 我的修改)。
- **rustc 1.86 vs 新 crate**:slave 裝 rustc 1.86,merge 的 Rust 套件常需 ≥1.88。修法:cargo `--locked` 用回 lock,或對 `cargo install` 釘版本。
- **trixie slave = Python 3.13 + NumPy 2.0**:打爛舊測試(`np.float_` 移除、`imp` 移除)。能 deselect/`_TEST=n` 就跳測;CLI code 真用到的才要改。
- **trixie pip 25.x `uninstall-no-record-file`**:對 apt 裝的無 RECORD 套件拒絕 uninstall。修法:裝 wheel 前 `pip install --ignore-installed [--no-deps] <那些套件>` 預給 RECORD。**切勿**對 SONiC wheel 安裝整包加 `--ignore-installed`(打斷 SONiC 互依)。
- **stale build 產物**:pre-merge(Jun 23)的 wheel/deb 對不上 Jun 26 merge source,make 未必偵測 → 必要時 `rm` 強制重建。
- **`make configure` 是 merge 前跑的**:trixie 的 `target/*/trixie` 子目錄沒建 → 手動 `mkdir -p`。
- **ZTP 啟用兩段式**:① `rules/config` 的 `ENABLE_ZTP = y`(預設被註解)決定 ztp deb 進不進 image;② wistron ZTP 補丁(1-based mapping 等)決定行為。少了①→`show ztp status` 回 unavailable。
- **`apply_patches.sh -z` 對現況會中途失敗**:sonic-ztp fork 已內含 mac-table 腳本、copporch.cpp 已含 dhcp_l2,那兩個補丁 forward/reverse dry-run 都衝突(其中 copporch 是 CRLF↔LF)→ script `set -e` 中止。**正解:手動只套真正缺的三個(1-based→sfp-10G→copp_cfg.j2),冗餘的跳過**(見 §3 Gate 6)。
- **CRLF 偵測別用 `$(printf "\r")`**:command substitution 會把 trailing CR 吃掉→空 pattern→`grep` 誤判全部 CRLF。用 `grep -qU $'\r' file`(bash ANSI-C quoting)才準。
- **1-based mapping 只改 port 名稱不改 lanes**:`Ethernet0..53`→`Ethernet1..54` 但 lanes 維持 0-based → **SAI XML lane 對映不受影響**(不會因此撞 PHA init);但 image 內建 config_db 的 port key 會變。
- 🚨 **device/ 與 platform sonic_platform/ 改動不會自動觸發 deb 重建(STALE-DEB 雷)**:make **不追蹤** `device/<板>/` 與 `sonic-platform-wistron/.../sonic_platform/` 下個別檔的變動。後果:**頭兩次「永久重建」其實都偷帶舊 device data**(image 是 0-based ports + 舊 27945B SAI XML,不是 1.14)。先前 DUT 會動只因為我在 DUT **直接 live-patch**,不是靠重燒。機制:① 所有 `device/.../*`(port_config、hwsku、**SAI/ASK XML**)打包進 **`sonic-device-data_1.0-1_all.deb`**(裝於 sonic_debian_extension.j2:299);② `eeprom.py`/`sfp.py`/整包 `sonic_platform` 編成 wheel 塞在 **`sonic-platform-es1227-54ts-p2_0.1_arm64.deb`** 裡。改 device/platform source 後**必須 `rm` 對應 deb 才會重建**——已寫進 `mig_build_image.sh` 的 rm 清單(device-data + platform-es,trixie+bookworm)。驗證:`dpkg-deb --fsys-tarfile <deb> | tar -xO ./usr/share/sonic/device/.../SAI-...xml | wc -c` 應為 **29853**(非 27945),且 in-deb port_config.ini 應為 1-based。
- 🚨🚨 **warm `systemctl restart swss/syncd` 會弄死 front-port link(此 Prestera 平台限制)**:在 DUT 上 `systemctl restart swss`(或 restart syncd)後,**所有 front port 永久 oper=down 不再起來**(netdev `NO-CARRIER`/`DORMANT`、TX=0;對端卻顯示 up = 不對稱)。實測 **1.17.1-1 與 1.17.1-2 兩版都一樣** → 不是 SAI 版本問題,是 warm restart **沒重置 Prestera PCI device / SerDes**。port **只有冷開機(`sudo reboot`)後才會 link up**(syslog 才有 `updatePortOperStatus ... set from down to up`)。**重大後果:任何「換 SAI / 改 dataplane 後重測 link 或 ARP」都必須冷開機,warm restart 測出來的 link-down 是假象、無效。** 容器內 `dpkg -i` 換的 SAI deb 會跨 reboot 保留(container writable layer 重用,HWSKU 不變不會 `docker rm`)。
- **容器內 `docker cp` 到 `/tmp` 會被 tmpfs 遮蓋**:syncd 的 `/tmp` 是 tmpfs mount,`docker cp file syncd:/tmp/` 回 0 但 `docker exec ls /tmp` 看不到(寫進底層被 mount 蓋掉)。改 cp 到 `/root/`(非掛載路徑)。

---

## 8. Gate 7:SAI 1.17.1-2 修 routed-port MAC→CPU / ARP(2026-06-30 驗證)

**症狀**:routed(無 VLAN)front port 上,對端送來的 ARP **上不了 DUT CPU** → ARP 不解析 → ping 不通(MAC-to-CPU 半路斷)。

**真因**:DUT 跑的 SAI **1.17.1-1(P7.0.0)缺 ARP port-based trapping 修正**。舊版 ARP trap 是 **VLAN-based**,routed/無 VLAN 的 port 不會把 ARP trap 到 CPU。P7.0.1 Release Notes **§3.4.1「ARP Trap Handling: VLAN-Based → Port-Based」/ bug SAIPRST-5514** 改成 port-based。另 §3.2.2.1 DHCP L2 改走 IPCL ACL(與 ZTP 相關)。

**修正**:換 **SAI 1.17.1-2(P7.0.1,EzB 1.14,對上我們的 1.14 XML)**。從 bazinga `…/SAI/SAI-P7.0.1/mrvllibsai_1.17.1-2_arm64.deb`(13451960 bytes)拉下。

**DUT 實測流程(關鍵:必須冷開機)**:
1. `docker cp` deb 進 syncd 容器(放 `/root/`,非 `/tmp`)→ `docker exec syncd dpkg -i --force-all`。
2. **`sudo reboot`**(冷開機 —— warm restart 會弄死 link,見 §7 雷)。容器 writable layer 跨 reboot 保留 1.17.1-2。
3. 冷開機後 Eth11 oper=**up**、SAI=1.17.1-2。配 routed:`config interface ip add Ethernet11 10.0.0.1/24`(對端 167 Eth27=10.0.0.2/24 routed)。
4. `ping -c5 10.0.0.2` → **5/5、ARP `10.0.0.2 ... REACHABLE`、TX/RX 計數有動** ✅。

**Source 變更(已進 repo,image 重建中)**:
- `platform/marvell-prestera/sai.mk`:arm64 `MRVL_SAI_VERSION` `1.17.1-1`→**`1.17.1-2`**;deb 已 copy 進 `platform/marvell-prestera/mrvllibsai_1.17.1-2_arm64.deb`。
- `sonic-platform-wistron/debian/rules`:**撤回** 之前誤加的 `CONFIG_KM_MVDMA2=y CONFIG_KM_MVETH=y`,回到原始 `CONFIG_KM_MVPCI=y CONFIG_KM_MVINT=y`。
- (排錯走過的錯路備忘:一度以為 MAC→CPU 斷是 mvcpss 缺 MVDMA2+MVETH packet datapath。**錯了** —— es1227 host SoC 是 **CN9130 + PCI-attached Prestera**,對應 Marvell 參考 `rd98dx35xx_cn9131`,只編 MVPCI+MVINT;MVETH/mvppnd 是給 on-chip AC5 板。dmaDriver2.c/ethDriver.c 的 6.12 移植留著無害但 flag 已關不編。)

---

## 9. 相關記憶 / 參考

- 完整逐步紀錄在 AI memory `trixie-kernel-migration.md`(本文件是其精華交接版)。
- 認證(lab 授權):**現役 DUT IP=192.168.80.174,admin 密碼=`YourPaSsWoRd`**(image `CHANGE_DEFAULT_PASSWORD=n` 預設;舊 `.182`/`.179` 已換機)。**對端測試機 peer=192.168.80.167,admin 密碼=`Prestera123`**(Eth27 routed 10.0.0.2/24,接 DUT Eth11)。bazinga `otis_lai@192.168.80.142`(經 jump 用 key 免密碼)。SAI P7.0.1 源:bazinga `/users_share/01_Project/200_Yamazaki/107_Switch_ASIC/SAI/SAI-P7.0.1/`。
- DUT 存取(build 機連不到 lab 子網,需經 jump host 10.36.243.105):`/home/otis/dut_run.sh '<cmd>'`、`dut_push_xml.sh`、`dut_apply_xml.sh`(皆用 `SSH_ASKPASS`+`setsid` 帶 DUT 密碼,`ProxyCommand="ssh -W %h:%p jump"`,`-o PubkeyAuthentication=no -o PreferredAuthentications=password` 避開 Too many auth failures)。

---

## 10. 2026-06-30 17:xx Update: mvSai / MVETH 回補與 DUT 驗證

### 10.1 修正結論

前一段 Gate 7 曾判斷「Wistron es1227 只需要 `CONFIG_KM_MVPCI=y CONFIG_KM_MVINT=y`,不需要 MVETH/MVDMA2」。這個判斷要修正:

- `platform/marvell-prestera/mrvl-prestera/drivers/generic/cpssEnabler/linuxNoKernelModule/drivers/Makefile` 第 8-14 行明確把 `mvSai` 放在 `ifeq ($(CONFIG_KM_MVETH), y)` 底下。
- 沒有 `CONFIG_KM_MVETH=y` 時,`mvSai.ko` 不會被編出來。
- `mvSai.ko` 是 SAI kernel interface。沒有它時,SAI kernel interface 缺失,會影響 switch/SDMA/CPU packet path。

所以 **Wistron es1227 的 Prestera generic module build 要保留 `MVPCI+MVINT`,並加回 `MVDMA2+MVETH`**:

```make
CONFIG_KM_MVPCI=y CONFIG_KM_MVINT=y CONFIG_KM_MVDMA2=y CONFIG_KM_MVETH=y
```

已修改:

- `platform/marvell-prestera/sonic-platform-wistron/debian/rules`
  - `MODULE_DIRS` 仍限定 `es1227_54ts_p2`
  - board module build 先 `clean` 再 `modules`
  - Prestera generic driver build flags 改為 `MVPCI+MVINT+MVDMA2+MVETH`
  - `binary-indep: build` 保持,確保不再 ship 舊 .ko

### 10.2 build 踩雷與處理

首次加回 `CONFIG_KM_MVETH=y` 後 build 失敗在:

```text
fatal error: opening dependency file .../drivers/.ethDriver.o.d: Permission denied
```

原因是 `mrvl-prestera/.../drivers/` 內有上一輪/root 產生的 stale artifact:

```text
.ethDriver.o.d
mvEthOpsDrv.mod
mvSai.mod
```

它們是 `root:root`,但 build 用 `otis` 寫入。解法:因目錄本身是 `otis` 可寫,不需 sudo,直接刪掉:

```bash
cd /home/otis/sonic-buildimage/platform/marvell-prestera/mrvl-prestera/drivers/generic/cpssEnabler/linuxNoKernelModule/drivers
rm -f .ethDriver.o.d mvEthOpsDrv.mod mvSai.mod
```

重跑 `/home/otis/mig_build_image.sh` 後成功通過 platform module build。

### 10.3 驗證結果: 新 deb 已含 mvSai

成功 build 後檢查:

```bash
dpkg-deb -c target/debs/trixie/sonic-platform-es1227-54ts-p2_0.1_arm64.deb \
  | grep -E 'mvSai\.ko|mvEthOpsDrv\.ko|mvcpss\.ko'
```

結果確認 deb 內有:

```text
/lib/modules/6.12.41+deb13-sonic-arm64/kernel/extra/mvcpss.ko
/lib/modules/6.12.41+deb13-sonic-arm64/kernel/extra/mvEthOpsDrv.ko
/lib/modules/6.12.41+deb13-sonic-arm64/kernel/extra/mvSai.ko
```

image build 成功:

```text
=== IMAGE BUILD DONE rc=0 Tue Jun 30 17:05:29 CST 2026 ===
target/sonic-marvell-prestera-arm64.bin 849M
ARTIFACT OK
```

### 10.4 DUT 192.168.80.174 驗證

燒到 DUT `.174` 後確認:

- SONiC `Wistron_M.1.0.2`
- Debian 13.5
- kernel `6.12.41+deb13-sonic-arm64`
- build date `Tue Jun 30 08:59:58 UTC 2026`
- platform `arm64-wistron_es1227_54ts_p2-r0`
- 13 個 containers 全部 Up
- port name 為 1-based: `Ethernet1..Ethernet54`
- `Ethernet49..54` 為 10G
- `Ethernet11` oper up
- ZTP 不再 unavailable:

```text
ZTP Admin Mode : True
ZTP Service    : Active Discovery
ZTP Status     : Not Started
```

但發現新問題:

- `mvSai.ko` / `mvEthOpsDrv.ko` / `mvcpss.ko` 都在 DUT 的 `/lib/modules/.../kernel/extra/`
- 開機後 `lsmod` 只看到 `mvcpss`,沒有 `mvSai`
- `/etc/modules-load.d/marvell.conf` 只有:

```text
mvcpss
psample
```

手動測試:

```bash
sudo modprobe mvSai
```

成功,`lsmod` 顯示:

```text
mvSai
psample
mvcpss
```

所以 `mvSai.ko` 本身可載入,缺的是開機自動載入清單。

### 10.5 自動載入 mvSai 的 source 修正

已修改 `mrvl-prestera` submodule 內:

```text
platform/marvell-prestera/mrvl-prestera/platform/arm64/913x/etc/modules-load.d/marvell.conf
```

加入:

```text
mvSai
```

注意:這個檔最後打包在 `mrvlprestera_1.0_arm64.deb`,不是 `sonic-platform-es1227-54ts-p2`。因此重建前必須額外清:

```bash
rm -f target/debs/trixie/mrvlprestera_*.deb target/debs/bookworm/mrvlprestera_*.deb
rm -f target/debs/trixie/mrvlprestera_*.deb.log target/debs/bookworm/mrvlprestera_*.deb.log
```

`mig_build_image.sh` 目前清了 image/SAI/syncd/device-data/platform-es,但還沒內建清 `mrvlprestera_*.deb`。若之後常改 `mrvl-prestera/platform/**`,建議把上面 rm 加進 script。

### 10.6 ping / ARP 測試結果

依 Claude 先前測試流程重跑:

- DUT `.174`: `Ethernet11 = 10.0.0.1/24`
- Peer `.167`: `Ethernet27 = 10.0.0.2/24`
- 從 DUT ping peer:

```bash
ping -c 5 -W 2 10.0.0.2
```

結果:

```text
5 packets transmitted, 5 received, 0% packet loss
PING_RC=0
10.0.0.2 lladdr 5c:ff:35:e7:00:b9 REACHABLE
```

Eth11 counters 有 TX/RX:

```text
RX packets nonzero
TX packets nonzero
```

這次測試是在手動 `modprobe mvSai` 後進行,因此 dataplane/ARP 功能已驗證 OK。下一顆 image 要確認的是「冷開機後 `mvSai` 自動載入」。

### 10.7 目前正在跑的最後一輪 build

為了把 `mrvl-prestera/platform/arm64/913x/etc/modules-load.d/marvell.conf` 的 `mvSai` 自動載入修正烤進 image,已再次執行:

```bash
bash /home/otis/mig_build_image.sh
```

啟動時間:

```text
Tue Jun 30 17:32:26 CST 2026
```

額外先手動清了:

```text
target/debs/{trixie,bookworm}/mrvlprestera_*.deb
```

目前進度在 trixie 階段,已看到:

```text
[ building ] [ target/debs/trixie/mrvlprestera_1.0_arm64.deb ]
[ finished ] [ target/debs/trixie/mrvlprestera_1.0_arm64.deb ]
```

這表示 `marvell.conf` 變更已重新打包進 `mrvlprestera` deb。接手者要繼續盯:

```bash
tail -f /home/otis/trixie_image_build.log
```

完成條件:

```text
=== IMAGE BUILD DONE rc=0 ...
ARTIFACT OK
```

燒新 image 後務必驗證冷開機狀態:

```bash
lsmod | egrep 'mvcpss|mvSai|mvEthOpsDrv'
cat /etc/modules-load.d/marvell.conf
show interface status Ethernet11
ping -c 5 -W 2 10.0.0.2
ip neigh show dev Ethernet11
```

### 10.8 2026-06-30 17:46 Update: ZTP helper telnetlib / Python 3.13 修正

DUT ZTP log 出現:

```text
sonic-ztp: Traceback (most recent call last):
sonic-ztp:   File "/usr/lib/ztp/add_macentry.py", line 2, in <module>
sonic-ztp:     from telnetlib import Telnet
sonic-ztp: ModuleNotFoundError: No module named 'telnetlib'
```

原因:trixie image 使用 Python 3.13,stdlib `telnetlib` 已移除。`src/sonic-ztp/src/usr/lib/ztp/` 內雖然有 bundled `telnetlib.py`,但 ZTP helper 直接 `from telnetlib import Telnet`,在實際執行方式下沒有可靠吃到同目錄版本。

已修改 5 個 helper,在 import 前顯式把 `__file__` 所在目錄塞進 `sys.path`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from telnetlib import Telnet
```

### 10.11 2026-06-30 18:15 Update: latest status at handoff tail

Latest build is still running, not failed. Current active processes:

```text
bash /home/otis/mig_build_image.sh
make ... target/sonic-marvell-prestera-arm64.bin
make BLDENV=trixie -f Makefile.work target/sonic-marvell-prestera-arm64.bin
docker run ... sonic-slave-trixie-otis:8c0bdf2fa87 ...
```

Main log currently ends at:

```text
[ finished ] [ target/debs/trixie/sonic-device-data_1.0-1_all.deb ]
basename: missing operand
Try 'basename --help' for more information.
[ building ] [ target/sonic-marvell-prestera-arm64.bin ]
```

The `basename` line has not stopped make. Inside the trixie slave container,
the image target is in rootfs/image assembly:

```text
./build_debian.sh
chroot ./fsroot-marvell-prestera apt-get -y install ...
```

Already verified before image assembly:

- `sonic-platform-es1227-54ts-p2_0.1_arm64.deb` contains `mvSai.ko`,
  `mvEthOpsDrv.ko`, and `mvcpss.ko`
- `es1227_54ts_p2_plt_setup.sh` writes `mvSai` into `/etc/modules-load.d/marvell.conf`
- `es1227_54ts_p2-init.sh` runs `modprobe mvSai`
- `sonic-ztp_1.0.0_all.deb` contains the local bundled `telnetlib.py` import fix
- `wistron_patches/ztp_workaround/0002-ztp-workaround-mac-table-added.patch` has the same ZTP fix
- `wistron_patches/0013-trixie-mvsai-kernel-interface.patch` was added and reverse-checks cleanly

Continue monitoring:

```bash
tail -f /home/otis/trixie_image_build.log
```

Completion target:

```text
=== IMAGE BUILD DONE rc=0 ...
ARTIFACT OK
```

### 10.10 2026-06-30 18:15 Update: current build status

最新狀態: build 仍在跑,不是失敗。因前面背景 `nohup` 方式會被 HUP/129 打斷,
最後一輪改成前景 tool session 跑:

```text
start=Tue Jun 30 17:58:54 CST 2026
```

目前已完成並驗證:

- bookworm `syncd_1.0.0_arm64.deb` 已完成
- `target/docker-syncd-mrvl-prestera.gz` 已完成並進入 trixie stage
- trixie `sonic-device-data_1.0-1_all.deb` 已完成
- trixie `sonic-platform-es1227-54ts-p2_0.1_arm64.deb` 已完成
- P2 deb 已抽包確認:
  - `es1227_54ts_p2_plt_setup.sh` 會寫 `mvSai`
  - `es1227_54ts_p2-init.sh` 會 `modprobe mvSai`
  - package 內含 `mvEthOpsDrv.ko`, `mvSai.ko`, `mvcpss.ko`
- `sonic-ztp_1.0.0_all.deb` 已抽包確認 `add_macentry.py` 使用 local bundled `telnetlib.py`

目前外層 log 最後看到:

```text
[ finished ] [ target/debs/trixie/sonic-device-data_1.0-1_all.deb ]
basename: missing operand
Try 'basename --help' for more information.
[ building ] [ target/sonic-marvell-prestera-arm64.bin ]
```

`basename: missing operand` 目前只是 warning,make 沒停。trixie slave container 內正在跑:

```text
./build_debian.sh
chroot ./fsroot-marvell-prestera apt-get -y install ...
```

接手後繼續盯:

```bash
tail -f /home/otis/trixie_image_build.log
```

完成條件仍是:

```text
=== IMAGE BUILD DONE rc=0 ...
ARTIFACT OK
```

如果要確認 process 還活著:

```bash
pgrep -af 'mig_build_image|sonic-marvell-prestera-arm64|Makefile.work|docker run.*sonic-slave'
docker ps
```

修改檔案:

- `src/sonic-ztp/src/usr/lib/ztp/add_macentry.py`
- `src/sonic-ztp/src/usr/lib/ztp/add_StaticFDBEntry_CPU63_from_ConfigDB.py`
- `src/sonic-ztp/src/usr/lib/ztp/add_StaticFDBEntry_CPU63_from_iplink.py`
- `src/sonic-ztp/src/usr/lib/ztp/add_StaticFDBEntry_CPU63_onVlan951.py`
- `src/sonic-ztp/src/usr/lib/ztp/add_StaticFDBEntry_CPU63_on_Vlan.py`

這些檔案打包在 `sonic-ztp_1.0-1_all.deb`,所以重建前必須清:

```bash
rm -f target/debs/trixie/sonic-ztp_*.deb target/debs/bookworm/sonic-ztp_*.deb
rm -f target/debs/trixie/sonic-ztp_*.deb.log target/debs/bookworm/sonic-ztp_*.deb.log
```

已停止上一輪 build,清掉 `sonic-ztp_*.deb` 與 `mrvlprestera_*.deb`,並重跑:

```bash
bash /home/otis/mig_build_image.sh
```

啟動時間:

```text
Tue Jun 30 17:46:21 CST 2026
```

這輪 image 目標同時包含:

1. `mvSai` auto-load (`mrvlprestera_1.0_arm64.deb`)
2. ZTP helper local `telnetlib.py` import fix (`sonic-ztp_1.0-1_all.deb`)

完成後燒 DUT 時,除了 §10.7 的冷開機驗證,還要看:

```bash
journalctl -b -u ztp --no-pager | grep -i 'telnetlib\|Traceback\|ModuleNotFound'
show ztp status
```

### 10.9 2026-06-30 17:58 Update: Wistron patch set / real mvSai auto-load path

更正 §10.7: `mrvlprestera_1.0_arm64.deb` 並沒有直接包含
`/etc/modules-load.d/marvell.conf`。ES1227-54TS-P2 實際會由
`sonic-platform-es1227-54ts-p2` package 的 postinst 執行:

```text
/usr/local/bin/es1227_54ts_p2_plt_setup.sh
```

該 script 會重寫 `/etc/modules-load.d/marvell.conf`。因此真正需要修改的是:

- `platform/marvell-prestera/sonic-platform-wistron/es1227_54ts_p2/scripts/es1227_54ts_p2_plt_setup.sh`
  - 追加 `echo "mvSai" >> $MODULE_FILE`
- `platform/marvell-prestera/sonic-platform-wistron/es1227_54ts_p2/scripts/es1227_54ts_p2-init.sh`
  - 在 `insmod .../mvcpss.ko` 後追加 `modprobe mvSai`

這樣同時覆蓋:

1. cold boot modules-load path
2. postinst 立即 `systemctl start es1227_54ts_p2-init.service` 的首次啟動 path

`wistron_patches/` 也已同步:

- 更新 `wistron_patches/ztp_workaround/0002-ztp-workaround-mac-table-added.patch`
  - 5 個 ZTP helper 都在 `from telnetlib import Telnet` 前加入 local `sys.path.insert(...)`
- 新增 `wistron_patches/0013-trixie-mvsai-kernel-interface.patch`
  - 記錄 `debian/rules` 的 `CONFIG_KM_MVDMA2=y CONFIG_KM_MVETH=y`
  - 記錄 P2 init/setup script 自動載入 `mvSai`

已驗證:

```text
git apply --reverse --check wistron_patches/0013-trixie-mvsai-kernel-interface.patch
PATCH_0013_REVERSE_CHECK_OK
```

最後一輪 build 已改成前景 session 跑,避免背景 job 收到 HUP:

```text
Tue Jun 30 17:58:54 CST 2026
```

等 `sonic-platform-es1227-54ts-p2_0.1_arm64.deb` 重新產出後要抽包驗:

```bash
dpkg-deb --fsys-tarfile target/debs/trixie/sonic-platform-es1227-54ts-p2_0.1_arm64.deb \
  | tar -xO ./usr/local/bin/es1227_54ts_p2_plt_setup.sh | grep -n mvSai

dpkg-deb --fsys-tarfile target/debs/trixie/sonic-platform-es1227-54ts-p2_0.1_arm64.deb \
  | tar -xO ./usr/local/bin/es1227_54ts_p2-init.sh | grep -n mvSai
```

已於 18:06 抽包確認:

```text
es1227_54ts_p2_plt_setup.sh: echo "mvSai" >> $MODULE_FILE
es1227_54ts_p2-init.sh:      modprobe mvSai
```

同一個 platform deb 也確認包含:

```text
mvEthOpsDrv.ko
mvSai.ko
mvcpss.ko
```

`sonic-ztp_1.0.0_all.deb` 抽 `add_macentry.py` 也確認包含:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from telnetlib import Telnet
```

### 10.12 2026-06-30 18:15 Update: latest status at file tail

Latest build is still running, not failed. Current active processes:

```text
bash /home/otis/mig_build_image.sh
make ... target/sonic-marvell-prestera-arm64.bin
make BLDENV=trixie -f Makefile.work target/sonic-marvell-prestera-arm64.bin
docker run ... sonic-slave-trixie-otis:8c0bdf2fa87 ...
```

Main log currently ends at:

```text
[ finished ] [ target/debs/trixie/sonic-device-data_1.0-1_all.deb ]
basename: missing operand
Try 'basename --help' for more information.
[ building ] [ target/sonic-marvell-prestera-arm64.bin ]
```

The `basename` line has not stopped make. Inside the trixie slave container,
the image target is in rootfs/image assembly:

```text
./build_debian.sh
chroot ./fsroot-marvell-prestera apt-get -y install ...
```

Already verified before image assembly:

- `sonic-platform-es1227-54ts-p2_0.1_arm64.deb` contains `mvSai.ko`,
  `mvEthOpsDrv.ko`, and `mvcpss.ko`
- `es1227_54ts_p2_plt_setup.sh` writes `mvSai` into `/etc/modules-load.d/marvell.conf`
- `es1227_54ts_p2-init.sh` runs `modprobe mvSai`
- `sonic-ztp_1.0.0_all.deb` contains the local bundled `telnetlib.py` import fix
- `wistron_patches/ztp_workaround/0002-ztp-workaround-mac-table-added.patch` has the same ZTP fix
- `wistron_patches/0013-trixie-mvsai-kernel-interface.patch` was added and reverse-checks cleanly

Continue monitoring:

```bash
tail -f /home/otis/trixie_image_build.log
```

Completion target:

```text
=== IMAGE BUILD DONE rc=0 ...
ARTIFACT OK
```
