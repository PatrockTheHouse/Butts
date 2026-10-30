"""Decode DS1 PARAM rows into named fields using the PARAMDEF binaries from Soulstruct's repo.

Usage:
  paramdecode.py <GameParam.parambnd> <ParamName> <rowid> [rowid ...]      # dump named rows
  paramdecode.py <GameParam.parambnd> <ParamName> --diff <base> <other...> # fields that differ from base
"""
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bnd3_list import parse_bnd3, parse_param  # noqa: E402

import modkit_env  # noqa: E402
PARAMDEF_BND = modkit_env.DS1_PARAMDEF_BND

TYPE_FMT = {
    "s8": ("b", 1), "u8": ("B", 1), "s16": ("h", 2), "u16": ("H", 2),
    "s32": ("i", 4), "u32": ("I", 4), "f32": ("f", 4), "dummy8": ("B", 1),
    "fixstr": (None, 1), "fixstrW": (None, 2), "b32": ("i", 4), "f64": ("d", 8),
}
BIT_LIMIT = {"u8": 8, "dummy8": 8, "s8": 8, "u16": 16, "s16": 16, "u32": 32, "s32": 32}


def cstr(b: bytes, enc="shift_jis") -> str:
    return b.split(b"\x00")[0].decode(enc, errors="replace")


def load_paramdefs():
    buf = PARAMDEF_BND.read_bytes()
    _, _, _, entries = parse_bnd3(buf)
    defs = {}
    for e in entries:
        d = buf[e["offset"]: e["offset"] + e["size"]]
        header_size, data_ver, field_count, field_size = struct.unpack_from("<hhhh", d, 4)
        ptype = cstr(d[0x0C:0x2C])
        fmt_ver = struct.unpack_from("<h", d, 0x2E)[0]
        assert fmt_ver == 104 and field_size == 0xB0, (ptype, fmt_ver, field_size)
        fields = []
        off = 0x30
        for _ in range(field_count):
            f = d[off: off + field_size]
            disp = cstr(f[0:0x40])
            dtype = cstr(f[0x40:0x48])
            dfmt = cstr(f[0x48:0x50])
            default, mn, mx, inc = struct.unpack_from("<4f", f, 0x50)
            edit_flags, size, desc_off = struct.unpack_from("<iii", f, 0x60)
            itype = cstr(f[0x6C:0x8C])
            name = cstr(f[0x8C:0xAC])
            sort_id = struct.unpack_from("<i", f, 0xAC)[0]
            desc = cstr(d[desc_off:]) if desc_off else ""
            bits = -1
            m = re.match(r"^\s*(.+?)\s*:\s*(\d+)\s*$", name)
            if m:
                name, bits = m.group(1), int(m.group(2))
            m2 = re.match(r"^\s*(.+?)\s*\[\s*(\d+)\s*]\s*$", name)
            arr = 1
            if m2:
                name, arr = m2.group(1), int(m2.group(2))
            fields.append(dict(name=name, disp=disp, dtype=dtype, size=size, bits=bits, arr=arr,
                               default=default, desc=desc, itype=itype, dfmt=dfmt))
            off += field_size
        defs[ptype] = fields
    return defs


def decode_row(data: bytes, fields):
    """Return ordered list of (name, value) following SoulsFormats bitfield packing rules."""
    out = []
    pos = 0
    bit_off = -1
    bit_type = None
    bit_val = 0
    for f in fields:
        t = f["dtype"]
        fmt, tsize = TYPE_FMT[t]
        if f["bits"] == -1:
            bit_off = -1
            if t in ("fixstr", "fixstrW"):
                raw = data[pos: pos + f["size"]]
                val = raw.decode("shift_jis" if t == "fixstr" else "utf-16-le", errors="replace").split("\x00")[0]
                pos += f["size"]
            elif t == "dummy8":
                raw = data[pos: pos + f["size"]]
                val = raw.hex() if any(raw) else None
                pos += f["size"]
            else:
                n = f["size"] // tsize
                vals = struct.unpack_from("<" + fmt * n, data, pos)
                val = vals[0] if n == 1 else list(vals)
                pos += f["size"]
            out.append((f["name"], val, f))
        else:
            limit = BIT_LIMIT[t]
            tg = "u8" if t == "dummy8" else t  # dummy8 bit-padding shares the byte with u8 bits
            if bit_off == -1 or bit_type != tg or bit_off + f["bits"] > limit:
                bit_off = 0
                bit_type = tg
                bit_val = struct.unpack_from("<" + fmt, data, pos)[0]
                pos += tsize
            val = (bit_val >> bit_off) & ((1 << f["bits"]) - 1)
            bit_off += f["bits"]
            out.append((f["name"], val, f))
    return out, pos


def get_param(bnd_path: Path, pname: str):
    buf = bnd_path.read_bytes()
    _, _, _, entries = parse_bnd3(buf)
    for e in entries:
        if e["name"].endswith("\\" + pname + ".param"):
            pbuf = buf[e["offset"]: e["offset"] + e["size"]]
            p = parse_param(pbuf)
            return pbuf, p
    raise SystemExit(f"no param {pname}")


def row_bytes(pbuf, p, idx):
    rows = p["rows"]
    start = rows[idx][1]
    end = rows[idx + 1][1] if idx + 1 < len(rows) else p["strings_off"]
    return pbuf[start:end]


def main():
    bnd = Path(sys.argv[1])
    pname = sys.argv[2]
    defs = load_paramdefs()
    pbuf, p = get_param(bnd, pname)
    fields = defs[p["type"]]
    by_id = {r[0]: i for i, r in enumerate(p["rows"])}

    def decoded(rid):
        idx = by_id[rid]
        vals, used = decode_row(row_bytes(pbuf, p, idx), fields)
        return p["rows"][idx][2], vals, used

    if sys.argv[3] == "--diff":
        base = int(sys.argv[4])
        others = [int(x) for x in sys.argv[5:]]
        bname, bvals, _ = decoded(base)
        cols = []
        for o in others:
            oname, ovals, _ = decoded(o)
            cols.append((o, oname, dict((n, v) for n, v, _ in ovals)))
        print(f"base: {base} {bname}")
        for o, oname, _ in cols:
            print(f"  vs {o} {oname}")
        print()
        for n, bv, f in bvals:
            diffs = [c[2][n] for c in cols]
            if any(d != bv for d in diffs):
                print(f"{n:<28} base={bv!s:<10} " + "  ".join(f"{d!s:<10}" for d in diffs) + f"   # {f['disp']}")
    else:
        for rid in [int(x) for x in sys.argv[3:]]:
            name, vals, used = decoded(rid)
            print(f"=== {pname} {rid} '{name}' (row size {p['row_size']}, decoded {used} bytes)")
            for n, v, f in vals:
                if v in (None, 0, 0.0, "", -1) and f["default"] in (0, -1):
                    continue
                print(f"  {n:<28} = {v!s:<14} # {f['disp']}")


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    main()
