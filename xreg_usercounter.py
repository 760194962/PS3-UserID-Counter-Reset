#!/usr/bin/env python3
"""
xreg_usercounter.py - inspect and patch the PS3 local user ID counter
(/setting/user/lastCreatedUserId) inside xRegistry.sys.

Commands
  info   FILE                 show the counter, registered users and the next expected ID
  set    FILE VALUE -o OUT    write a patched copy with a new counter value (u2 CRC fixed)
  verify OLD NEW              check the CRC-16 model against every entry that changed
                              between two dumps of the same console
  saved  VALUE -o OUT         write a 4-byte savedLastCreatedUserId file

The input file is never modified. Python 3.8+, standard library only.
See README.md / docs/findings.md for the format notes and the procedure.
"""
import argparse
import hashlib
import re
import struct
import sys

FILE_SIZE = 0x40000
MAGIC = b"\xBC\xAD\xAD\xBC"
KEY_TABLE = 0x10
VALUE_TABLE = 0x10000
END = b"\xAA\xBB\xCC\xDD\xEE"
KEY_LAST_CREATED = "/setting/user/lastCreatedUserId"
USER_RE = re.compile(r"^/setting/user/(\d{8})$")


# --------------------------------------------------------------------------- CRC
def crc16_raw(data: bytes) -> int:
    """CRC-16, poly 0x1021, MSB-first, init 0, no xorout (the linear part)."""
    c = 0
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) if c & 0x8000 else (c << 1)
            c &= 0xFFFF
    return c


def u2_delta(old_data: bytes, old_tail: int, new_data: bytes, new_tail: int) -> int:
    """XOR change of u2 when an entry's data/tail change but the length does not.
    u2 is an affine CRC over data that ends with <data><tail>, so the change only
    depends on the XOR difference of those bytes."""
    diff = bytes(a ^ b for a, b in zip(old_data + bytes([old_tail]), new_data + bytes([new_tail])))
    return crc16_raw(diff)


# ------------------------------------------------------------------------ parse
def load(path):
    try:
        with open(path, "rb") as f:
            d = f.read()
    except OSError as e:
        sys.exit(f"{path}: {e.strerror}")
    if len(d) != FILE_SIZE or d[:4] != MAGIC or d[12:16] != MAGIC:
        sys.exit(f"{path}: not an xRegistry.sys (size/magic mismatch)")
    return d


def parse_keys(d):
    """Return {entry_offset: (key_type, name)}. Resyncs over deleted-entry garbage."""
    keys = {}
    n = KEY_TABLE
    while n < VALUE_TABLE - 5:
        if d[n:n + 5] == END:
            n += 7
            if d[n:n + 16] == b"\x00" * 16:
                break
            continue
        _id, klen, ktype = struct.unpack(">HHB", d[n:n + 5])
        name = d[n + 5:n + 5 + klen]
        if (0 < klen < 256 and ktype <= 3 and name[:1] == b"/"
                and all(32 <= c < 127 for c in name) and d[n + 5 + klen] == 0):
            keys[n] = (ktype, name.decode("ascii"))
            n += 5 + klen + 1
        else:
            n += 1
    return keys


def parse_values(d, keys):
    """Return list of dicts for value entries whose key_offset points at a known key."""
    vals = []
    n = VALUE_TABLE
    while n < FILE_SIZE - 10:
        if d[n:n + 5] == END:
            n += 7
            continue
        if d[n:n + 9] == b"\x00" * 9:
            n += 1
            continue
        _u1, koff, u2, vlen = struct.unpack(">HHHH", d[n:n + 8])
        vtype = d[n + 8]
        kpos = koff + 0x10
        if vtype <= 2 and vlen <= 0x400 and kpos in keys and n + 10 + vlen <= FILE_SIZE:
            vals.append(dict(off=n, key_off=kpos, name=keys[kpos][1], u2=u2, type=vtype,
                             data=d[n + 9:n + 9 + vlen], tail=d[n + 9 + vlen]))
            n += 10 + vlen
        else:
            n += 1
    return vals


def find_int(d, keys, vals, name):
    kpos = [o for o, (_t, k) in keys.items() if k == name]
    if len(kpos) != 1:
        return None
    hits = [v for v in vals if v["key_off"] == kpos[0] and v["type"] == 1 and len(v["data"]) == 4]
    if len(hits) != 1:
        sys.exit(f"expected exactly one value entry for {name}, found {len(hits)}")
    return hits[0]


def registered_users(keys):
    """IDs whose top-level key /setting/user/XXXXXXXX is present (deleted users lose it)."""
    return sorted(int(m.group(1)) for _t, k in keys.values() for m in [USER_RE.match(k)] if m)


def ival(v):
    return struct.unpack(">i", v["data"])[0]


# --------------------------------------------------------------------- commands
def cmd_info(a):
    d = load(a.file)
    keys = parse_keys(d)
    vals = parse_values(d, keys)
    lc = find_int(d, keys, vals, KEY_LAST_CREATED)
    users = registered_users(keys)
    print(f"file      : {a.file}  md5 {hashlib.md5(d).hexdigest()}")
    print(f"entries   : {len(keys)} keys, {len(vals)} values")
    print(f"{KEY_LAST_CREATED} = {ival(lc)}   (entry @0x{lc['off']:X}, u2 0x{lc['u2']:04X}, tail 0x{lc['tail']:02X})")
    for nm in ("/setting/user/lastLoginUserId", "/setting/user/defaultLoginUserId"):
        v = find_int(d, keys, vals, nm)
        if v:
            print(f"{nm} = {ival(v)}")
    print("registered users : " + (", ".join(f"{u:08d}" for u in users) or "(none)"))
    nxt = max([ival(lc)] + users) + 1
    print(f"next user ID (observed rule max(counter, highest user)+1): {nxt:08d}")


def cmd_set(a):
    src = load(a.file)
    keys = parse_keys(src)
    vals = parse_values(src, keys)
    lc = find_int(src, keys, vals, KEY_LAST_CREATED)
    if lc["tail"] != 0:
        print(f"note: tail byte is 0x{lc['tail']:02X} (usually 00); keeping it unchanged")
    old = ival(lc)
    new = a.value
    if not 0 <= new < 100000000:
        sys.exit("value must be 0..99999999")
    users = registered_users(keys)
    if users and new < max(users):
        print(f"warning: users up to {max(users):08d} are still registered; the console will "
              f"still hand out {max(users) + 1:08d} next (see README).")
    newdata = struct.pack(">I", new)
    nu2 = lc["u2"] ^ u2_delta(lc["data"], lc["tail"], newdata, lc["tail"])
    d = bytearray(src)
    o = lc["off"]
    d[o + 4:o + 6] = struct.pack(">H", nu2)
    d[o + 9:o + 13] = newdata
    with open(a.out, "wb") as f:
        f.write(d)
    changed = [i for i in range(FILE_SIZE) if d[i] != src[i]]
    print(f"{KEY_LAST_CREATED}: {old} -> {new}   u2 0x{lc['u2']:04X} -> 0x{nu2:04X}   (entry @0x{o:X})")
    print("bytes changed : " + ", ".join(f"0x{i:X}" for i in changed))
    print(f"md5 in  : {hashlib.md5(src).hexdigest()}")
    print(f"md5 out : {hashlib.md5(d).hexdigest()}")
    print("upload the output to BOTH /dev_flash2/etc/xRegistry.sys and /dev_flash2/etc/backup/xRegistry.sys")


def cmd_verify(a):
    A, B = load(a.old), load(a.new)
    va = {v["off"]: v for v in parse_values(A, parse_keys(A))}
    vb = {v["off"]: v for v in parse_values(B, parse_keys(B))}
    ok = bad = 0
    for off in sorted(set(va) & set(vb)):
        x, y = va[off], vb[off]
        if x["key_off"] != y["key_off"] or len(x["data"]) != len(y["data"]):
            continue
        if (x["u2"], x["data"], x["tail"]) == (y["u2"], y["data"], y["tail"]):
            continue
        pred = x["u2"] ^ u2_delta(x["data"], x["tail"], y["data"], y["tail"])
        good = pred == y["u2"]
        ok += good
        bad += not good
        print(f"{'OK  ' if good else 'FAIL'} @0x{off:X} {x['name']}: data {x['data'].hex()}->{y['data'].hex()} "
              f"tail {x['tail']:02X}->{y['tail']:02X}  u2 {x['u2']:04X}->{y['u2']:04X} (predicted {pred:04X})")
    print(f"\n{ok} rewritten entries match the CRC-16 model, {bad} do not")
    sys.exit(1 if bad else 0)


def cmd_saved(a):
    with open(a.out, "wb") as f:
        f.write(struct.pack(">I", a.value))
    print(f"wrote {a.out}: {struct.pack('>I', a.value).hex(' ')}  -> /dev_flash2/etc/savedLastCreatedUserId")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    q = s.add_parser("info"); q.add_argument("file"); q.set_defaults(f=cmd_info)
    q = s.add_parser("set"); q.add_argument("file"); q.add_argument("value", type=int)
    q.add_argument("-o", "--out", required=True); q.set_defaults(f=cmd_set)
    q = s.add_parser("verify"); q.add_argument("old"); q.add_argument("new"); q.set_defaults(f=cmd_verify)
    q = s.add_parser("saved"); q.add_argument("value", type=int)
    q.add_argument("-o", "--out", required=True); q.set_defaults(f=cmd_saved)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
