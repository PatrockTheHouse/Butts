"""Elden Ring / DS3-style TAE (version 0x1000D, 64-bit) reader and writer with byte-exact round trip.

Model
-----
TAE(tae_id, skeleton_name, sib_name, unk30, flags, animations)
Animation(id, events, groups, file_type, file_a, file_b, file_name)
Event(start, end, type, payload: bytes)
Group(type, event_indices)

Usage:
    tae3.py roundtrip FILE.tae [...]     # read + write, report byte-identical or first difference
    tae3.py dump FILE.tae                # brief listing
"""
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Event:
    start: float
    end: float
    type: int
    payload: bytes


@dataclass
class Group:
    type: int
    event_indices: list[int]


@dataclass
class Animation:
    id: int
    events: list[Event] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    file_type: int = 0          # 0 = standard (HKX name below), 1 = reference to another animation
    file_a: int = 0             # standard: flags (0x100 = imports HKX of file_b) / reference: source animation ID
    file_b: int = -1            # standard: imported animation ID or -1 / reference: 0
    file_name: str = ""         # e.g. "a046_031030.hkt"
    file_tail: bytes = b"\x00" * 16


@dataclass
class TAE:
    tae_id: int
    skeleton_name: str
    sib_name: str
    unk30: int
    flags: bytes
    animations: list[Animation]


# Event group "family" for generated animations: most common vanilla group type per event type.
GROUP_FAMILY = {
    34: 32, 35: 32, 110: 96, 113: 112, 114: 96, 115: 96, 118: 96, 119: 96, 120: 96, 129: 128, 133: 128, 134: 128,
    138: 136, 139: 136, 145: 144, 151: 150, 193: 192, 237: 229, 238: 232, 300: 0, 304: 1, 307: 1, 320: 0, 331: 330,
    341: 1, 511: 510, 601: 0, 603: 600, 604: 600, 706: 700, 707: 700, 712: 710, 717: 700, 718: 700, 731: 730,
    761: 760, 782: 799, 787: 786, 788: 786, 789: 786, 790: 96, 792: 96, 800: 600, 901: 900, 902: 900, 906: 900,
    907: 765, 911: 317,
}


def group_family(event_type: int) -> int:
    return GROUP_FAMILY.get(event_type, event_type)


_MODE_SIZES: dict[int, int] | None = None


def mode_sizes() -> dict[int, int]:
    """Most common payload size per event type, measured from vanilla Elden Ring TAEs (data/)."""
    global _MODE_SIZES
    if _MODE_SIZES is None:
        import json
        p = Path(__file__).resolve().parent.parent / "data" / "er_tae_event_sizes.json"
        _MODE_SIZES = {int(k): v for k, v in json.loads(p.read_text()).items()} if p.exists() else {}
    return _MODE_SIZES


# ------------------------------------------------------------------------------------------------ reading

def _u16(buf: bytes, off: int) -> str:
    end = off
    while buf[end:end + 2] != b"\x00\x00":
        end += 2
    return buf[off:end].decode("utf-16-le")


def read_tae(buf: bytes) -> TAE:
    assert buf[:4] == b"TAE ", buf[:4]
    version = struct.unpack_from("<I", buf, 8)[0]
    assert version == 0x1000D, hex(version)
    q = lambda o: struct.unpack_from("<Q", buf, o)[0]  # noqa: E731
    unk30 = q(0x30)
    flags = bytes(buf[0x40:0x48])
    tae_id, anim_count = struct.unpack_from("<ii", buf, 0x50)
    anims_off, groups_off = q(0x58), q(0x60)
    skeleton_name = _u16(buf, q(0xB0))
    sib_name = _u16(buf, q(0xB8))
    animations = []
    for k in range(anim_count):
        aid, aoff = struct.unpack_from("<qQ", buf, anims_off + 16 * k)
        ev_hdr, grp, times, afile, nev, ngrp, nt, _z = struct.unpack_from("<QQQQiiii", buf, aoff)
        # events
        hdr_offsets = []
        events = []
        datas = []
        for i in range(nev):
            h = ev_hdr + 24 * i
            st_off, et_off, d_off = struct.unpack_from("<QQQ", buf, h)
            hdr_offsets.append(h)
            datas.append(d_off)
            events.append((struct.unpack_from("<f", buf, st_off)[0], struct.unpack_from("<f", buf, et_off)[0], d_off))
        # payload sizes from gaps between consecutive data entries (data is contiguous), last one ends at the
        # event-group table (or at the next 16-byte boundary before it)
        sorted_datas = sorted(datas)
        end_of_data = grp if ngrp else None
        ev_objs = []
        msizes = mode_sizes()
        for st, et, d_off in events:
            typ, poff = struct.unpack_from("<qQ", buf, d_off)
            idx = sorted_datas.index(d_off)
            if idx + 1 < len(sorted_datas):
                size = sorted_datas[idx + 1] - poff
            else:
                # last payload: the group table (or file end) is 16-aligned and payloads are multiples of 8, so the
                # only ambiguity is the final 8 bytes; use the type's most common size to decide.
                limit = end_of_data if end_of_data is not None else len(buf)
                avail = limit - poff
                m = msizes.get(typ)
                if m is not None and m in (avail, avail - 8):
                    size = m
                elif avail >= 8 and buf[poff + avail - 8:poff + avail] == b"\x00" * 8 and (m is None or m < avail):
                    size = avail - 8
                else:
                    size = avail
            ev_objs.append(Event(st, et, typ, bytes(buf[poff:poff + size])))
        # groups
        groups = []
        for g in range(ngrp):
            cnt, voff, toff, _z2 = struct.unpack_from("<QQQQ", buf, grp + 32 * g)
            gtype = q(toff)
            members = [struct.unpack_from("<i", buf, voff + 4 * m)[0] for m in range(cnt)]
            groups.append(Group(gtype, [hdr_offsets.index(h) for h in members]))
        # animation file record
        ftype = q(afile)
        name_off = q(afile + 16)
        fa, fb = struct.unpack_from("<ii", buf, afile + 24)
        tail = bytes(buf[afile + 32:afile + 48])
        fname = _u16(buf, name_off) if name_off else ""
        animations.append(Animation(aid, ev_objs, groups, ftype, fa, fb, fname, tail))
    return TAE(tae_id, skeleton_name, sib_name, unk30, flags, animations)


# ------------------------------------------------------------------------------------------------ writing

def _pad16(b: bytearray):
    while len(b) % 16:
        b.append(0)


def write_tae(tae: TAE) -> bytes:
    out = bytearray(0xD0)
    strings = bytearray()
    skel_off = 0xD0
    s = tae.skeleton_name.encode("utf-16-le") + b"\x00\x00"
    strings += s
    _pad16(strings)
    sib_off = 0xD0 + len(strings)
    strings += tae.sib_name.encode("utf-16-le") + b"\x00\x00"
    _pad16(strings)
    out += strings
    n = len(tae.animations)
    anims_off = len(out)
    out += b"\x00" * (16 * n)                      # animation table (filled later)
    groups_off = len(out)
    # animation groups: maximal runs of consecutive animation ids
    runs = []
    for k, a in enumerate(tae.animations):
        if runs and a.id == tae.animations[runs[-1][1]].id + 1:
            runs[-1][1] = k
        else:
            runs.append([k, k])
    out += struct.pack("<QQ", len(runs), (groups_off + 16) if runs else 0)
    for first, last in runs:
        out += struct.pack("<iiQ", tae.animations[first].id, tae.animations[last].id, anims_off + 16 * first)
    first_anim = len(out)
    out += b"\x00" * (48 * n)                       # animation structs (filled later)
    for k, a in enumerate(tae.animations):
        afile = len(out)
        name_off = afile + 48
        out += struct.pack("<QQQii", a.file_type, afile + 16, name_off, a.file_a, a.file_b) + a.file_tail
        out += a.file_name.encode("utf-16-le") + b"\x00\x00"
        if a.file_name:
            _pad16(out)          # vanilla pads non-empty names to 16; an empty name is just its terminator
        # times (unique float32 values, ascending)
        times = sorted({_f32(e.start) for e in a.events} | {_f32(e.end) for e in a.events})
        times_off = len(out)
        time_pos = {t: times_off + 4 * i for i, t in enumerate(times)}
        for t in times:
            out += struct.pack("<f", t)
        _pad16(out)
        ev_hdr = len(out)
        hdr_pos = [ev_hdr + 24 * i for i in range(len(a.events))]
        data_pos = []
        pos = ev_hdr + 24 * len(a.events)
        for e in a.events:
            data_pos.append(pos)
            pos += 16 + len(e.payload)
        for e, d in zip(a.events, data_pos):
            out += struct.pack("<QQQ", time_pos[_f32(e.start)], time_pos[_f32(e.end)], d)
        for e, d in zip(a.events, data_pos):
            out += struct.pack("<qQ", e.type, d + 16) + e.payload
        _pad16(out)
        grp_off = len(out)
        out += b"\x00" * (32 * len(a.groups))
        for gi, g in enumerate(a.groups):
            toff = len(out)
            out += struct.pack("<QQ", g.type, 0)
            voff = len(out)
            for idx in g.event_indices:
                out += struct.pack("<i", hdr_pos[idx])
            _pad16(out)
            struct.pack_into("<QQQQ", out, grp_off + 32 * gi, len(g.event_indices), voff, toff, 0)
        struct.pack_into("<QQQQiiii", out, first_anim + 48 * k,
                         ev_hdr if a.events else 0, grp_off if a.groups else 0, times_off if times else 0, afile,
                         len(a.events), len(a.groups), len(times), 0)
        struct.pack_into("<qQ", out, anims_off + 16 * k, a.id, first_anim + 48 * k)
    # header
    hdr = struct.pack("<4s4sIi", b"TAE ", b"\x00\x00\x00\xff", 0x1000D, len(out))
    hdr += struct.pack("<QQQQQQ", 0x40, 1, 0x50, 0x80, tae.unk30, 0)
    hdr += tae.flags + struct.pack("<Q", 1)
    hdr += struct.pack("<iiQQQ", tae.tae_id, n, anims_off if n else 0, groups_off, 0xA0)
    hdr += struct.pack("<iiQQQ", n, 0, first_anim if n else 0, 1, 0x90)
    hdr += struct.pack("<iiQQQQQ", tae.tae_id, tae.tae_id, 0x50, 0, 0xB0, skel_off, sib_off)
    hdr += b"\x00" * 16
    assert len(hdr) == 0xD0, len(hdr)
    out[:0xD0] = hdr
    return bytes(out)


def _f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


# ------------------------------------------------------------------------------------------------ CLI

def roundtrip(path: Path) -> str:
    raw = path.read_bytes()
    tae = read_tae(raw)
    out = write_tae(tae)
    if out == raw:
        return f"{path.name}: OK ({len(raw)} bytes, {len(tae.animations)} animations)"
    n = min(len(raw), len(out))
    first = next((i for i in range(n) if raw[i] != out[i]), n)
    return (f"{path.name}: DIFF at {first:#x} (sizes {len(raw)} vs {len(out)}); "
            f"orig={raw[first:first+16].hex(' ')} new={out[first:first+16].hex(' ')}")


def main():
    cmd = sys.argv[1]
    if cmd == "roundtrip":
        ok = 0
        for p in sys.argv[2:]:
            r = roundtrip(Path(p))
            ok += r.endswith(")") and ": OK" in r
            if ": OK" not in r or len(sys.argv) <= 3:
                print(r)
        print(f"{ok}/{len(sys.argv) - 2} byte-identical")
    elif cmd == "dump":
        tae = read_tae(Path(sys.argv[2]).read_bytes())
        print(f"TAE {tae.tae_id} skel={tae.skeleton_name} sib={tae.sib_name} unk30={tae.unk30:#x} flags={tae.flags.hex()}")
        for a in tae.animations:
            print(f"  {a.id:>7} type={a.file_type} a={a.file_a} b={a.file_b} file={a.file_name!r} events={len(a.events)} groups={len(a.groups)}")


if __name__ == "__main__":
    main()
