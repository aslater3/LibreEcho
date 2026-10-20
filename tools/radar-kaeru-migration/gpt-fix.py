#!/usr/bin/env python3
"""Convert a legacy `_x` radar GPT to the plain (pinned Kaeru) layout.

  boot_a_x/boot_b_x (p10/p11, 16 MiB)  -> renamed boot_a/boot_b, same sectors
  boot_a/boot_b     (p17/p18, ~110 MiB wrapper) -> entries removed
  userdata and everything else          -> untouched (same start/end)

This is the layout of a working pinned-Kaeru unit; the Kaeru LK resolves the
partition NAME `boot_a`, it has no `_x` redirect.

Usage: gpt-fix.py primary.bin backup.bin out_primary.bin out_backup.bin [--show]
  primary.bin = LBA 0..33 (34 sectors), backup.bin = last 33 sectors.
Stdlib only. Exit 0 = converted files written, 3 = already plain, 2 = refused.
"""
import struct, sys, zlib

SS = 512


def die(msg, rc=2):
    print("GPT-FIX REFUSED: " + msg)
    sys.exit(rc)


def parse_header(sector):
    if sector[:8] != b"EFI PART":
        die("no GPT signature")
    (rev, hsize, hcrc, _r, my, alt, first, last, guid, ent_lba, nent, esize,
     ecrc) = struct.unpack("<IIIIQQQQ16sQIII", sector[8:92])
    if hsize != 92:
        die("unexpected GPT header size %d" % hsize)
    chk = bytearray(sector[:92]); chk[16:20] = b"\0\0\0\0"
    if zlib.crc32(bytes(chk)) != hcrc:
        die("GPT header CRC invalid")
    return dict(my=my, alt=alt, first=first, last=last, guid=guid, ent_lba=ent_lba,
                nent=nent, esize=esize, ecrc=ecrc)


def build_header(h, my, alt, ent_lba, ecrc):
    hdr = bytearray(b"EFI PART" + struct.pack("<IIIIQQQQ16sQIII", 0x10000, 92, 0, 0, my, alt,
                    h["first"], h["last"], h["guid"], ent_lba, h["nent"], h["esize"], ecrc))
    hdr[16:20] = struct.pack("<I", zlib.crc32(bytes(hdr)))
    return bytes(hdr) + b"\0" * (SS - 92)


def entries(raw, h):
    out = []
    for i in range(h["nent"]):
        e = raw[i * h["esize"]:(i + 1) * h["esize"]]
        tg, g, s, en, at, nm = struct.unpack("<16s16sQQQ72s", e[:128])
        out.append(dict(i=i + 1, tg=tg, g=g, s=s, e=en, at=at, nm=nm,
                        name=nm.decode("utf-16le").split("\0")[0], rest=e[128:]))
    return out


def pack(ents, h):
    raw = b""
    for e in ents:
        raw += struct.pack("<16s16sQQQ72s", e["tg"], e["g"], e["s"], e["e"], e["at"], e["nm"]) + e["rest"]
    return raw


def main():
    if len(sys.argv) < 5:
        die("usage: gpt-fix.py primary.bin backup.bin out_primary.bin out_backup.bin [--show]")
    prim = open(sys.argv[1], "rb").read()
    back = open(sys.argv[2], "rb").read()
    show = "--show" in sys.argv
    h = parse_header(prim[SS:2 * SS])
    ent_sectors = (h["nent"] * h["esize"] + SS - 1) // SS
    if h["ent_lba"] != 2 or len(prim) < (2 + ent_sectors) * SS:
        die("unexpected primary entry array location/size")
    raw = prim[2 * SS:(2 + ent_sectors) * SS]
    if zlib.crc32(raw[:h["nent"] * h["esize"]]) != h["ecrc"]:
        die("primary entry array CRC invalid")
    bh = parse_header(back[-SS:])
    if bh["my"] != h["alt"] or bh["alt"] != 1:
        die("backup header does not match primary")
    braw = back[-SS - ent_sectors * SS:-SS]
    if braw != raw:
        die("primary and backup entry arrays differ; refusing to guess which is right")

    ents = entries(raw, h)
    by = {e["name"]: e for e in ents if e["name"]}
    if "boot_a_x" not in by and "boot_b_x" not in by:
        print("GPT already plain (no boot_*_x partitions); nothing to do.")
        sys.exit(3)
    need = ["boot_a_x", "boot_b_x", "boot_a", "boot_b", "userdata", "lk_a", "lk_b", "expdb"]
    miss = [n for n in need if n not in by]
    if miss:
        die("missing expected partitions: %s" % miss)
    for n in ("boot_a_x", "boot_b_x"):
        if by[n]["e"] - by[n]["s"] + 1 != 32768:
            die("%s is not 32768 sectors" % n)
    for n in ("boot_a", "boot_b"):
        if by[n]["e"] - by[n]["s"] + 1 <= 32768:
            die("%s is not the larger wrapper partition" % n)
        if by[n]["s"] <= by["userdata"]["e"]:
            die("%s is not appended after userdata" % n)
    ud = by["userdata"]
    if ud["e"] - ud["s"] + 1 != 2137088:
        die("userdata is %d sectors, not the contract 2137088" % (ud["e"] - ud["s"] + 1))

    def table(es):
        return "\n".join("p%-2d %-12s %9d-%9d  %8d sectors" % (e["i"], e["name"], e["s"], e["e"],
                         e["e"] - e["s"] + 1) for e in es if e["name"])
    if show:
        print("BEFORE:\n" + table(ents))

    for e in ents:
        if e["name"] in ("boot_a", "boot_b"):          # drop wrappers
            e.update(tg=b"\0" * 16, g=b"\0" * 16, s=0, e=0, at=0, nm=b"\0" * 72, name="")
    for e in ents:
        if e["name"] in ("boot_a_x", "boot_b_x"):      # rename stores
            nn = e["name"][:-2]
            e["name"] = nn
            e["nm"] = nn.encode("utf-16le").ljust(72, b"\0")
    if show:
        print("AFTER:\n" + table(ents))

    nraw = pack(ents, h)
    ecrc = zlib.crc32(nraw[:h["nent"] * h["esize"]])
    nprim = prim[:SS] + build_header(h, 1, h["alt"], 2, ecrc) + nraw + prim[(2 + ent_sectors) * SS:]
    nback = nraw + build_header(h, h["alt"], 1, h["alt"] - ent_sectors, ecrc)
    # re-parse what we are about to write
    parse_header(nprim[SS:2 * SS]); parse_header(nback[-SS:])
    if len(nprim) != len(prim) or len(nback) != (ent_sectors + 1) * SS:
        die("internal size check failed")
    open(sys.argv[3], "wb").write(nprim)
    open(sys.argv[4], "wb").write(nback)
    print("GPT-FIX OK")


main()
