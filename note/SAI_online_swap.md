# 線上替換 syncd 容器內的 Marvell SAI（不重燒 image）

> 環境:marvell-prestera arm64 DUT(SONiC),經跳板 ssh `admin@<DUT-IP>`(密碼為 lab admin 密碼)。
> 用途:把新的 `mrvllibsai_*.deb` 換進**執行中的** syncd 容器測試,免重 build / 重燒。
> 實測日期:2026-06-25(.179,SAI P5.0.0 / `mrvllibsai 1.15.1-1` 同版號不同 build)。

---

## 0. 先判斷:這次換「能不能線上做」

| 情況 | 風險 | 做法 |
|------|------|------|
| **同 SAI API 版本、不同 build**(例:1.15.1-1 → 1.15.1-1 respin) | 低 | 本筆記流程,`dpkg -i` 要 `--force-all` |
| **SAI 版本升級**(例:1.15 → **1.16**) | 高 | libsai 與 syncd/libsairedis 的 SAI API 不匹配,syncd 可能 `exit 1`。**正規做法是重 build syncd image 再燒**;線上只能「試」,且務必盯 log |

關鍵:`syncd` / `libsairedis` 是**對特定 SAI header 版本編譯**的。只換 runtime `libsai.so` 而不重編 syncd,SAI 版本一變就可能 ABI/API 不合。deb 裡的 `usr/include/sai/*.h` 是給編譯用的,runtime 不吃。

**預判相容性**(版本升級前先對):
```bash
# 現役(容器內)
docker exec syncd cat /usr/include/sai/saiversion.h | grep -E 'SAI_(MAJOR|MINOR|REVISION)'
# 新 deb
dpkg-deb --fsys-tarfile <new.deb> | tar -xO ./usr/include/sai/saiversion.h | grep -E 'SAI_(MAJOR|MINOR|REVISION)'
```

---

## 1. 確認是不是「真的不一樣」(同版號時必做)

同版號但來源不同 → 比對**實際檔案內容**,不是比版號。deb 常常**沒有 md5sums control 檔**(`dpkg-deb -e` 會撲空),所以直接對 sha256 最準:

```bash
# 在 DUT 上:把 deb payload 攤開,逐檔 sha256 對容器內已安裝檔
dpkg-deb -x <new.deb> /tmp/saix
cd /tmp/saix
find . -type f | sed 's|^\./||' | while read -r rel; do
  new=$(sha256sum "/tmp/saix/$rel" | awk '{print $1}')
  cur=$(docker exec syncd sha256sum "/$rel" 2>/dev/null | awk '{print $1}')
  [ "$new" != "$cur" ] && echo "DIFFER /$rel"
done
```
實測 1.15.1-1 兩個 build:107 檔中 6 檔不同 →
`usr/lib/libsai.so`、`usr/bin/mrvlcmd`、4 個 ASIC 韌體(`ac5x/ac5pIpfixFw.fw`、`mvHwsServiceCpuCm3Ac5/RavenFw.fw`)。其餘 header/.srds/多數韌體相同。

---

## 2. 把 deb 灌進容器並安裝

⚠️ **`docker cp` 在這環境會默默失敗**(裝完發現 libsai.so hash 沒變就是中這個雷)。改用 **stdin 串流**最穩:

```bash
DEB=/tmp/mrvllibsai_x.y.z_arm64.deb        # 已在 DUT host /tmp
docker exec -i syncd bash -c 'cat > /tmp/newsai.deb' < "$DEB"
docker exec syncd ls -l /tmp/newsai.deb     # 確認真的進去了

# 同版號要 --force-all 才會覆蓋;版本升級則不用 force(正常 upgrade)
docker exec syncd dpkg -i --force-all /tmp/newsai.deb

# 驗證:容器內 libsai.so 的 sha256 應變成新 deb 的值
docker exec syncd sha256sum /usr/lib/libsai.so
```

**裝完一定要驗 hash 變了再往下**;沒變就是沒裝進去(多半是上面 docker cp 的雷)。

---

## 3. commit 進 image(撐過容器重建 / 重開機)

syncd 服務重啟時 `syncd.sh` 會 **`docker rm -f` + `docker create/run`**(從 image 重建容器),所以不 commit 的話,一 restart 就把剛裝的 deb 洗掉。

```bash
docker commit syncd docker-syncd-mrvl-prestera:latest
docker commit syncd docker-syncd-mrvl-prestera:Wistron.M.1.0.1   # 兩個 tag 同一 image id,都蓋
```
commit 後:重建容器、**reboot** 都會用到新 SAI(reboot 也是從 image 重建)。
**但重燒一顆新 build 的 image 會還原** → 要永久帶,得把 deb 放進 image build。

---

## 4. 重啟讓新 SAI 生效(資料面會中斷)

cold restart syncd 會讓 orchagent/swss 失去同步 → **swss 會自己 Exited(0)**,屬正常,要再 restart swss 收尾:

```bash
sudo systemctl restart syncd      # syncd 重建(~60s 重新 init ASIC),swss 會跟著 Exited
sudo systemctl restart swss       # 重新建立 orchagent <-> syncd 同步(會連帶把 syncd 一起拉)
```
> 實務上直接 `sudo systemctl restart swss` 一發即可,它會連帶重啟 syncd;分兩步只是看得清楚發生什麼。

---

## 5. 驗證

```bash
docker ps -a --format '{{.Names}} :: {{.Status}}' | grep -iE 'syncd|swss|bgp|teamd'
# 期望:syncd / swss / bgp / teamd 全 Up

docker exec syncd sha256sum /usr/lib/libsai.so          # 仍是新 hash = 跑的是 commit 過的 image
docker exec syncd dpkg -s mrvllibsai | grep ^Version
docker exec swss supervisorctl status | grep -E 'orchagent|portsyncd|vlanmgrd'   # RUNNING
docker logs syncd --tail 20 | grep -iE 'err|fail|abort|fatal'   # 應為空

# 健康指標:syncd log 出現 "syncd entered RUNNING state" 且沒 exit;
# 反例:syncd "exited (exit status 1; not expected)" = SAI/init 出事(版本升級不相容就會這樣)
```

---

## 注意事項彙整

- **同版號**:`dpkg -i` 要 `--force-all`;**升級**:不用 force。
- **`docker cp` 不可靠** → 一律用 `docker exec -i ... cat > file` 串流。
- **沒 commit 就 restart = 白裝**(容器重建會還原)。
- **資料面會中斷**(syncd/swss 重啟);非 warmboot,流量會斷一下。
- **不持久於重燒**:要進正式 image 得放進 build。
- **版本升級(如 1.16)**:線上只能試,盯 syncd log;不相容就回去重 build syncd image,讓 libsai / libsairedis / syncd 同版本。
- 取得/比對 SAI 版本看 `usr/include/sai/saiversion.h`(header 只供編譯,不影響 runtime)。
