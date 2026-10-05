# PS3 User ID Counter Reset (`lastCreatedUserId`)

Every local user on a PS3 gets a folder `/dev_hdd0/home/XXXXXXXX`. The number comes from a counter in the system registry (`/dev_flash2/etc/xRegistry.sys`, key `/setting/user/lastCreatedUserId`) that only ever goes up. On NAND consoles (CECHA/B/C/E) the registry lives in the internal flash, so formatting the HDD or doing *Restore PS3 System* does not reset the counter.

This repo documents how the counter actually works and how to reset it. It also includes a small tool that patches the counter and fixes the checksum.

**Tested on:** one CECHA01 (NAND), Evilnat 4.93 CFW with Cobra 8.5, webMAN MOD FTP. October 2026.

> ⚠️ **You are editing the system registry on your console's internal flash.** The PS3 cannot boot without a valid registry. Back up `/dev_flash2/etc` first, keep the backup, and only continue if you can recover from a mistake. This was tested on a single console. Use at your own risk.

## Key findings

1. **The 2-byte field before each value is a CRC-16 (poly `0x1021`).** psdevwiki lists it as `unk_value_2` ("entry ID or 16-bit checksum?").
   - The byte after each value is **not** a fixed `00` terminator. It is part of the checksummed data.
   - The exact covered range and init value are still unknown. But the CRC is affine, so after changing a value the new checksum can be computed exactly without knowing them:
     `new_u2 = old_u2 XOR crc16_raw((old_bytes ⊕ new_bytes) + (old_tail ⊕ new_tail))`
   - The model matched all 7 entries the OS rewrote in the dumps I compared. Two of those were the OS's own counter writes, and they were predicted **before** they happened.
2. **No other checksum covers the whole file.** When a user is created, only the new entries get appended and the counter entry gets rewritten in place.
3. **Writing via FTP works.** You can upload directly to `/dev_flash2/etc/xRegistry.sys` and `/dev_flash2/etc/backup/xRegistry.sys`. The OS always writes both copies with identical content. A patched file survives a reboot and the OS uses the patched value.
4. **Next user ID = max(counter, highest registered user ID) + 1.** Lowering the counter does nothing while a higher-numbered user still exists.
5. **`/dev_flash2/etc/savedLastCreatedUserId`** (4 bytes, big-endian) carries the counter across *Restore PS3 System*. It is not updated when a user is created.
6. **Attaching an HDD that already has `home/XXXXXXXX` folders re-imports those users on boot** and raises the counter to the highest imported ID.

Details, raw observations and the evidence for each point: [docs/findings.md](docs/findings.md).

## Requirements

- A PS3 on CFW that exposes `/dev_flash2` over FTP with write access. Here that was webMAN MOD on Evilnat 4.93.
- Python 3.8+ on a PC. The tool uses the standard library only.
- A copy of your CFW PUP and webMAN MOD pkg on a USB stick, if you plan to do a Restore (procedure A).

## Procedure A: back to `00000001` (needs Restore PS3 System)

The last remaining user can't be deleted, so the only way back to `00000001` is a Restore. **The Restore formats the HDD.**

1. Download the whole `/dev_flash2/etc` folder and keep it as a backup.
2. Patch the counter to 0:
   ```
   python xreg_usercounter.py info xRegistry.sys
   python xreg_usercounter.py set  xRegistry.sys 0 -o patched/xRegistry.sys
   python xreg_usercounter.py saved 0 -o patched/savedLastCreatedUserId
   ```
3. Upload `patched/xRegistry.sys` to **both** `/dev_flash2/etc/xRegistry.sys` and `/dev_flash2/etc/backup/xRegistry.sys`. Upload `savedLastCreatedUserId` to `/dev_flash2/etc/`.
4. Reboot right away. Don't change any settings or create users first.
5. Download the files again and check them with `info`, or compare MD5 against what `set` printed.
6. Boot the Recovery Menu: from standby, hold power until the second beep and then the quick double beep. Choose **5. Restore PS3 System** (PS3™の初期化). Don't choose *Restore Default Settings*; it doesn't remove users.
7. Finish the initial setup. The first user is `00000001`.
8. Reinstall webMAN MOD (or your FTP plugin) from USB. It lived on the HDD that was just formatted.

## Procedure B: continue from N+1 without a Restore

Use this when you're keeping a low-numbered user (e.g. `00000001`) and want the next one to be `00000002`.

1. **Delete the higher-numbered users in the XMB.** Don't rename or delete their `home/` folders over FTP beforehand. See [findings §7](docs/findings.md#7-deleting-users) for what happened when I did.
2. Download `xRegistry.sys` and run `info`. Only the users you want to keep should be listed.
3. `set` the counter to the highest remaining ID, e.g. `1`. Upload the result to both locations and reboot.
4. Create a user. With `00000001` left and the counter set to 1, it becomes `00000002`.

## Tool

```
python xreg_usercounter.py info   FILE               # counter, users, expected next ID
python xreg_usercounter.py set    FILE VALUE -o OUT  # patched copy, u2 recalculated
python xreg_usercounter.py verify OLD NEW            # test the CRC model on two dumps
python xreg_usercounter.py saved  VALUE -o OUT       # 4-byte savedLastCreatedUserId
```

- The tool finds entries by key name, not by fixed offset. In my file the counter entry sat at `0x11ACE` in one dump and `0x11B64` in another.
- It never modifies the input file.
- `set` warns you if a higher-numbered user is still registered.

**Never publish your `xRegistry.sys`.** Besides settings, it contains your PSN sign-in fields, Wi-Fi passphrase and HDD serial.

## Open questions

- Which byte range does the CRC cover, and with what init value? Only the linear part is pinned down, but that is enough to patch values.
- What sets the tail byte? It changes on some rewrites even when the value doesn't.
- When *Restore PS3 System* rebuilds the counter, does it read `savedLastCreatedUserId` or the old registry? I set both to 0, so this run can't tell. Also, after this Restore the file was gone, but the previous owner's restore had left one behind.
- Does the "next ID" rule look at registry entries or at `home/` folders on the HDD?
- NOR consoles (registry in VFLASH on the HDD) haven't been tested.

## References

- psdevwiki: [XRegistry.sys](https://www.psdevwiki.com/ps3/XRegistry.sys) (file structure, settings list, NAND/NOR notes) and [Talk:VSH](https://www.psdevwiki.com/ps3/Talk:VSH) (`xsetting` / `xUser` interfaces)
- PSX-Place: [Clean install from CFW](https://www.psx-place.com/threads/clean-install-from-cfw.22408/). sandungas on the counter surviving Restore; return42's report of it resetting.
- Registry parsers: [ManaGunZ `xreg.c`](https://github.com/Zarh/ManaGunZ/blob/master/MGZ/source/xreg.c), [IRISMAN `sysregistry.c`](https://github.com/Estwald/irismanager-4-x/blob/master/source/sysregistry.c)

---

### 中文简介

PS3 本地用户文件夹 `dev_hdd0/home/XXXXXXXX` 的编号来自 xRegistry.sys 里的 `lastCreatedUserId`，这个计数器只增不减。NAND 机型的 registry 在机内闪存里，格式化硬盘或"PS3 初始化"都清不掉它。

本仓库记录了以下发现：

- 值条目前的 2 字节是 CRC-16（多项式 0x1021）。利用 CRC 的线性，改值后能直接算出新的校验值。
- 用 FTP 可以直接写 `dev_flash2`，主文件和 `backup` 两份要一起改。
- 新用户编号 = max(计数器, 现有最大用户编号) + 1。
- `savedLastCreatedUserId` 负责在"PS3 初始化"时把计数器带过去。
- 接回旧硬盘时，系统会把硬盘上已有的用户重新导入 registry。

操作方法见上方的 Procedure A（回到 00000001，需要初始化）和 Procedure B（不初始化，从 N+1 继续）。
