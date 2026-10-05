# Findings in detail

All observations come from one CECHA00 (NAND) on Evilnat 4.93 / Cobra 8.5. I pulled `/dev_flash2/etc` over FTP between each step and compared the dumps byte by byte. Offsets are from my file and **will differ on yours**. The tool finds entries by key name.

## 1. Value entry layout

Value table entries start at `0x10000`. This matches the psdevwiki layout, except for the last byte:

| Offset | Size | Field | Notes |
|---|---|---|---|
| +0 | 2 | `u1` | always 0 here |
| +2 | 2 | `key_offset` | key entry position − 0x10 |
| +4 | 2 | **`u2`** | **CRC-16, see below** (psdevwiki: `unk_value_2`) |
| +6 | 2 | `length` | |
| +8 | 1 | `type` | 0 bool/raw, 1 int (big-endian), 2 string |
| +9 | `length` | data | |
| +9+len | 1 | **`tail`** | usually `00`, but **not a fixed terminator**: seen as `1E`, `6D`, `72`, `03`, and it is covered by the CRC |

`lastCreatedUserId` example (counter = 5):

```
00 00 | 51 C1 | 14 07 | 00 04 | 01 | 00 00 00 05 | 00
 u1     key_off   u2     len   type    value        tail
```

## 2. `u2` is an affine CRC-16 (poly 0x1021, MSB-first)

Matching `u2` directly against standard CRCs of the value, the key name, or both gave 0–1 hits out of 428 entries. Identical values under different keys also have different `u2`. So the CRC covers more than the value alone. I haven't identified the exact range or init value.

The rewrites the OS performed show the CRC's linearity directly. When an entry is rewritten with the same length, `u2` changes by `crc16_raw` of the XOR difference of `data + tail`:

| Entry rewritten by the OS | data | tail | u2 old → new | Δu2 | `crc16_raw(diff)` |
|---|---|---|---|---|---|
| `/setting/system/autoPowerOff` | 0 → 0 | `1E`→`00` | `326C`→`C193` | `F3FF` | `crc16_raw(1E)` = `F3FF` ✓ |
| `/setting/system/autoPowerOffEx` | 0 → 0 | `1E`→`00` | `CB6C`→`3893` | `F3FF` | `F3FF` ✓ |
| `/setting/user/<id>/browser/interlaceFilter` | 0 → 0 | `6D`→`72` | `4C51`→`AF8F` | `E3DE` | `crc16_raw(1F)` = `E3DE` ✓ |
| `/setting/system/language` (zh-TW → ja) | 10 → 8 | `00` | `3423`→`5241` | `6662` | `crc16_raw(02 00)` = `6662` ✓ |
| `/setting/user/lastLoginUserId` | 1 → 1 | `00`→`03` | `5B04`→`6B67` | `3063` | ✓ |
| `/setting/user/lastCreatedUserId` (OS created a user) | 5 → 6 | `00` | `1407`→`4154` | | ✓ **predicted before the user was created** |
| `/setting/user/lastCreatedUserId` (OS created a user) | 7 → 8 | `00` | `7265`→`625B` | | ✓ **predicted before the user was created** |

Note that `F3FF ⊕ E3DE = 1021 = crc16_raw(01)`. This is exactly the linearity you expect from a CRC with poly 0x1021.

So for any value change:

```
new_u2 = old_u2 ^ crc16_raw( (old_data ^ new_data) + bytes([old_tail ^ new_tail]) )
crc16_raw: poly 0x1021, MSB-first, init 0, no reflection, no xorout
```

Precomputed `u2` for `lastCreatedUserId` with tail `00` (it doesn't depend on the entry position):

| value | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| u2 | EBF2 | D8C3 | 8D90 | BEA1 | 2736 | 1407 | 4154 | 7265 | 625B | 516A | 0439 |

These were all either observed in the OS's own writes or accepted by the OS after patching.

`python xreg_usercounter.py verify OLD NEW` re-checks this on your own dumps.

## 3. No file-level integrity check

When the OS created a user, these were the only changes:

- the new user's ~68 keys appended to the key table, and its ~47 values appended to the value table;
- the end markers moving;
- the `lastCreatedUserId` entry rewritten in place (`u2` + value).

The header (`BC AD AD BC 00 00 00 90 00 00 00 02 BC AD AD BC`) and the 16 bytes at `0xFFF0` (`4D 26 00 7A 4D 26 00 62 00 04 …`) did not change.

**Main and backup copies.** In the 5 cases where I downloaded both `/dev_flash2/etc/xRegistry.sys` and `/dev_flash2/etc/backup/xRegistry.sys` after the OS had written the registry, the two were byte-identical. Those writes covered the original state, a language change and three user creations. Some things I can't tell:

- whether the OS updates both at the same moment, or syncs the backup later (e.g. at shutdown);
- what it does if the two differ;
- when it actually falls back to the backup. psdevwiki lists its purpose as unknown.

I always uploaded the same patched file to both locations, so a mismatched pair was never tested.

## 4. Writing via FTP, and the allocation rule

Patched files uploaded over FTP to both locations, followed by an immediate reboot, survive. Re-downloading after the reboot gave a byte-identical file.

| Counter in registry | Registered users | New user got | Notes |
|---|---|---|---|
| 5 (original) | 4, 5 | `00000006` | OS wrote 5→6 |
| **7** (patched) | 4, 5, 6 | `00000008` | proves the patched value is read |
| **0** (patched) | 4, 5, 6, 8 | `00000009` | upload not independently verified |
| **0** (patched, re-downloaded after reboot) | 4, 5, 6, 8, 9 | `00000010` | the counter is **not** the only input |
| **0** + `savedLastCreatedUserId` 0, then Restore | none | `00000001` | |
| **1** (patched) | 1 | `00000002` | |

The observed rule is **next = max(lastCreatedUserId, highest registered user ID) + 1**. The OS then writes the new ID back as the counter. IDs are decimal: `00000009` → `00000010`.

## 5. `savedLastCreatedUserId`

- `/dev_flash2/etc/savedLastCreatedUserId` is 4 bytes, big-endian.
- On my console it held `3`, timestamped the day the previous owner did a restore before selling it. The lowest user ID on the console when I got it was `00000004`, which fits a restore that carried the counter (3) over.
- It did **not** change across any of my user creations.
- After my own *Restore PS3 System*, with the file set to 0 and the registry counter set to 0, the file was gone.

Interpretation (not proven): *Restore PS3 System* saves the counter into this file and merges it back into the rebuilt registry. That's why a Restore alone never resets the counter on NAND consoles. sandungas guessed in 2019 that the counter was "backed up temporarily" during a Restore.

## 6. Attaching an HDD that already has users

After the Restore, the registry had only `00000001`. I then put back an older HDD that still had `home/00000004` and `home/00000005`. After boot:

- users 4 and 5 were registered again, with `*` prepended to their names;
- `lastCreatedUserId` became 5.

So swapping drives, or restoring an HDD backup, can push the counter back up.

## 7. Deleting users

- **Deleting a user in the XMB** removes its top-level key `/setting/user/XXXXXXXX`. The key gets overwritten with the end marker `AA BB CC DD EE 00 00`, so `etting/user/XXXXXXXX` is left visible right after it. The user's subkeys stay in the table as dead data. This is the "trash data from erased users" on psdevwiki. It caused no problems.
- In one case I renamed a user's `home/` folder over FTP **before** deleting the user in the XMB. The top-level key was still removed correctly, but a second, complete set of subkeys for that ID showed up, with a blank name and avatar. These are dead data. The user didn't appear in the XMB, and creating new users afterwards worked fine. I haven't tested what happens if you only delete the folder over FTP and never delete the user in the XMB.

The tool counts a user as registered only if its top-level key is present. Leftover subkeys don't count.

## 8. Things that are not known

- The exact CRC coverage and init value.
- What sets the tail byte.
- Whether the Restore reads the counter from `savedLastCreatedUserId` or from the old registry. Both were 0 in my run.
- Why the previous owner's `savedLastCreatedUserId` stayed around for a year but mine disappeared after the Restore.
- Whether "highest registered user" is taken from the registry or from `home/` folders.
- Behaviour on NOR consoles, where psdevwiki says xRegistry.sys lives in VFLASH on the HDD.
