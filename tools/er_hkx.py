"""Havok 2018 tagfile (Elden Ring HKX) surgery: section walker and TYPE-section transplant.

    import er_hkx
    out = er_hkx.transplant_types(our_hkx_bytes, donor_hkx_bytes)   # our DATA/INDX + the donor's TYPE section
    er_hkx.sections(buf)                                              # [(depth, tag, offset, size, flags), ...]

Why: Soulstruct's tagfile packer writes a correct but *different* type table from FromSoft's exporter (older section
magics TNAM/TBOD instead of TNA1/TBDY, no `unsigned short` base type, fewer type-body flags). The game's own player
animation files (c0000_a?x.anibnd.dcx) all carry TNA1/TBDY tables, so a converted animation is made indistinguishable
from a vanilla one by replacing its TYPE section with the TYPE section of a vanilla animation that uses the same
classes (any spline-compressed c0000 clip), remapping the type indices in the ITEM and PTCH sections by structural
type identity (type name + template arguments, recursively). The DATA section is untouched.

Tagfile layout (big-endian section headers): u32 (flags << 30 | size incl. header) + 4-char tag. TAG0 > SDKV, DATA,
TYPE (> TPTR, TSTR, TNA1|TNAM, FSTR, TBDY|TBOD, THSH, TPAD) or TCRF, INDX (> ITEM, PTCH). ITEM: 12-byte entries
(u32 type index | flags << 24, u32 data offset, u32 count); PTCH: (u32 type index, u32 count, u32 offsets[count])*.
"""
import struct
import sys
from pathlib import Path

CONTAINER_TAGS = {b"TAG0", b"TYPE", b"INDX"}


def sections(buf: bytes, off: int = 0, end: int | None = None, depth: int = 0, out=None):
    out = [] if out is None else out
    end = len(buf) if end is None else end
    pos = off
    while pos + 8 <= end:
        hdr = struct.unpack_from(">I", buf, pos)[0]
        size, flags = hdr & 0x3FFFFFFF, hdr >> 30
        tag = buf[pos + 4:pos + 8]
        out.append((depth, tag, pos, size, flags))
        if tag in CONTAINER_TAGS:
            sections(buf, pos + 8, pos + size, depth + 1, out)
        pos += size
    return out


def section(buf: bytes, *tags: bytes) -> bytes | None:
    """Payload (without the 8-byte header) of the first section with one of the given tags."""
    for _, tag, pos, size, _ in sections(buf):
        if tag in tags:
            return buf[pos + 8:pos + size]
    return None


def _section_raw(buf: bytes, *tags: bytes) -> bytes:
    for _, tag, pos, size, _ in sections(buf):
        if tag in tags:
            return buf[pos:pos + size]
    raise KeyError(tags)


def pack_section(tag: bytes, payload: bytes, flags: int = 1) -> bytes:
    return struct.pack(">I", (flags << 30) | (len(payload) + 8)) + tag + payload


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    b = buf[pos]
    if b < 0x80:
        return b, pos + 1
    if b < 0xC0:
        return ((b & 0x3F) << 8) | buf[pos + 1], pos + 2
    if b < 0xE0:
        return ((b & 0x1F) << 16) | (buf[pos + 1] << 8) | buf[pos + 2], pos + 3
    if b < 0xF0:
        return ((b & 0x0F) << 24) | (buf[pos + 1] << 16) | (buf[pos + 2] << 8) | buf[pos + 3], pos + 4
    raise ValueError(f"unsupported varint lead byte {b:#x}")


def type_table(buf: bytes) -> list[tuple[str, tuple] | None]:
    """[None] + (name, ((template name, template value), ...)) per type, from TSTR + TNA1/TNAM."""
    tstr = section(buf, b"TSTR").decode("utf-8").split("\0")
    tnam = section(buf, b"TNA1", b"TNAM")
    count, pos = read_varint(tnam, 0)
    out = [None]
    for _ in range(count - 1):
        name_idx, pos = read_varint(tnam, pos)
        n_tpl, pos = read_varint(tnam, pos)
        templates = []
        for _ in range(n_tpl):
            tname_idx, pos = read_varint(tnam, pos)
            value, pos = read_varint(tnam, pos)
            templates.append((tstr[tname_idx], value))
        out.append((tstr[name_idx], tuple(templates)))
    return out


def type_keys(table) -> list:
    """Structural identity of each type: name + templates with type-valued templates expanded recursively."""
    cache = {}

    def key(i):
        if i == 0:
            return None
        if i in cache:
            return cache[i]
        name, templates = table[i]
        cache[i] = ("...",)  # cycle guard (self-referential templates do not occur, but be safe)
        k = (name, tuple((tn, key(tv) if tn.startswith("t") else ("v", tv)) for tn, tv in templates))
        cache[i] = k
        return k

    return [key(i) for i in range(len(table))]


def transplant_types(target: bytes, donor: bytes) -> bytes:
    """`target` with its TYPE section replaced by `donor`'s; ITEM/PTCH type indices remapped by structural identity."""
    t_keys = type_keys(type_table(target))
    d_keys = type_keys(type_table(donor))
    d_index = {}
    for i, k in enumerate(d_keys):
        d_index.setdefault(k, i)
    remap = {0: 0}
    missing = []
    for i, k in enumerate(t_keys):
        if i == 0:
            continue
        if k in d_index:
            remap[i] = d_index[k]
        else:
            missing.append(k)
    used = set()
    item = bytearray(section(target, b"ITEM"))
    for off in range(0, len(item), 12):
        ref, data_off, count = struct.unpack_from("<III", item, off)
        idx, flags = ref & 0xFFFFFF, ref >> 24
        used.add(idx)
        if idx not in remap:
            raise ValueError(f"ITEM references type {idx} {t_keys[idx]} missing from donor")
        struct.pack_into("<III", item, off, (flags << 24) | remap[idx], data_off, count)
    ptch = section(target, b"PTCH")
    entries = []
    pos = 0
    while pos < len(ptch):
        idx, count = struct.unpack_from("<II", ptch, pos)
        pos += 8
        offsets = ptch[pos:pos + 4 * count]
        pos += 4 * count
        used.add(idx)
        if idx not in remap:
            raise ValueError(f"PTCH references type {idx} {t_keys[idx]} missing from donor")
        entries.append((remap[idx], count, offsets))
    entries.sort(key=lambda e: e[0])
    new_ptch = b"".join(struct.pack("<II", i, c) + o for i, c, o in entries)
    # unused missing types are harmless (not referenced by data), but say so
    if any(k in missing for k in (t_keys[i] for i in used)):
        raise ValueError("used type missing from donor")
    secs = sections(target)
    flags_of = {tag: fl for _, tag, _, _, fl in secs}
    sdkv = _section_raw(target, b"SDKV")
    data = _section_raw(target, b"DATA")
    type_sec = _section_raw(donor, b"TYPE")
    indx_payload = pack_section(b"ITEM", bytes(item), flags_of.get(b"ITEM", 1)) + pack_section(b"PTCH", new_ptch, flags_of.get(b"PTCH", 1))
    indx = pack_section(b"INDX", indx_payload, flags_of.get(b"INDX", 0))
    return pack_section(b"TAG0", sdkv + data + type_sec + indx, flags_of.get(b"TAG0", 0))


def describe(buf: bytes) -> str:
    return " | ".join(("  " * d) + f"{tag.decode('ascii', 'replace')} {size}" for d, tag, _, size, _ in sections(buf))


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--transplant":
        target, donor, out = (Path(p) for p in args[1:4])
        res = transplant_types(target.read_bytes(), donor.read_bytes())
        out.write_bytes(res)
        print(f"{out}: {len(res)} bytes; {describe(res)}")
        return
    for p in args:
        print(f"{p}: {describe(Path(p).read_bytes())}")


if __name__ == "__main__":
    main()
