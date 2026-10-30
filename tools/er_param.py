"""Read Elden Ring PARAM files (64-bit layout) using Paramdex XML paramdefs and row-name lists."""
import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bnd_tools import parse_bnd4  # noqa: E402

import modkit_env  # noqa: E402
PARAMDEX = modkit_env.PARAMDEX / "ER"
TYPE_FMT = {"s8": ("b", 1), "u8": ("B", 1), "s16": ("h", 2), "u16": ("H", 2), "s32": ("i", 4), "u32": ("I", 4),
            "f32": ("f", 4), "f64": ("d", 8), "s64": ("q", 8), "u64": ("Q", 8), "b32": ("i", 4), "a16": ("H", 2),
            "dummy8": ("B", 1), "fixstr": (None, 1), "fixstrW": (None, 2)}
BIT_LIMIT = {"u8": 8, "s8": 8, "dummy8": 8, "u16": 16, "s16": 16, "u32": 32, "s32": 32}
DEF_RE = re.compile(r"^\s*(\w+)\s+(\w+)(?:\[(\d+)\])?(?::(\d+))?(?:\s*=\s*(.+?))?\s*$")


_DEF_BY_TYPE: dict[str, Path] | None = None

# Elden Ring params whose file name differs from their paramdef file name (ER params carry no ParamType string).
DEF_ALIASES = {
    "AtkParam_Pc": "AtkParam", "AtkParam_Npc": "AtkParam", "BehaviorParam_PC": "BehaviorParam",
    "Bullet": "BulletParam", "Magic": "MagicParam", "SpEffectParam": "SpEffect", "SpEffectVfxParam": "SpEffectVfx",
    "ItemLotParam_enemy": "ItemLotParam", "ItemLotParam_map": "ItemLotParam",
    "GrassTypeParam_Lv1": "GrassTypeParam", "GrassTypeParam_Lv2": "GrassTypeParam",
    "ShopLineupParam_Recipe": "ShopLineupParam", "MenuValueTableParam": "MenuValueTableSpecParam",
    "AssetEnvironmentGeometryParam": "AssetGeometryParam", "MapGdRegionInfoParam": "MapGdRegionInfo",
    "WwiseValueToStrParam_BgmBossChrIdConv": "WwiseValueToStrConvertParamFormat",
}


def def_path_for(def_name: str, param_type: str | None = None) -> Path:
    """Paramdex def file for a param: by file name (EquipParamWeapon.xml), alias table, or ParamType string."""
    global _DEF_BY_TYPE
    direct = PARAMDEX / "Defs" / f"{def_name}.xml"
    if direct.exists():
        return direct
    alias = DEF_ALIASES.get(def_name) or re.sub(r"_(ps4|ps5|xboxone|scarlett)$", "", def_name)
    if alias != def_name and (PARAMDEX / "Defs" / f"{alias}.xml").exists():
        return PARAMDEX / "Defs" / f"{alias}.xml"
    if _DEF_BY_TYPE is None:
        _DEF_BY_TYPE = {}
        for x in (PARAMDEX / "Defs").glob("*.xml"):
            try:
                t = ET.parse(x).getroot().findtext("ParamType")
            except ET.ParseError:
                continue
            if t:
                _DEF_BY_TYPE[t] = x
    if param_type and param_type in _DEF_BY_TYPE:
        return _DEF_BY_TYPE[param_type]
    raise FileNotFoundError(f"no paramdef for {def_name} (type {param_type!r})")


def param_type_of(buf: bytes) -> str:
    """ParamType string stored in a 64-bit PARAM header."""
    flags2d = buf[0x2D]
    if flags2d & 0x80:  # OffsetParamType
        off = struct.unpack_from("<Q", buf, 0x30)[0]
        end = buf.index(b"\0", off)
        return buf[off:end].decode("ascii", errors="replace")
    return buf[0x40:0x60].split(b"\0")[0].decode("ascii", errors="replace")


def load_def(def_name: str, param_type: str | None = None):
    """def_name like 'EquipParamWeapon'. Returns (param_type, fields)."""
    root = ET.parse(def_path_for(def_name, param_type)).getroot()
    ptype = root.findtext("ParamType")
    fields = []
    for f in root.iter("Field"):
        m = DEF_RE.match(f.get("Def"))
        t, name, arr, bits, default = m.groups()
        fields.append(dict(type=t, name=name, arr=int(arr) if arr else 1, bits=int(bits) if bits else -1,
                           default=default, enum=f.findtext("Enum") or "", disp=f.findtext("DisplayName") or ""))
    return ptype, fields


def field_size(f):
    fmt, tsize = TYPE_FMT[f["type"]]
    return tsize * f["arr"]


def decode_row(data: bytes, fields):
    out = {}
    pos = 0
    bit_off, bit_type, bit_val = -1, None, 0
    for f in fields:
        t = f["type"]
        fmt, tsize = TYPE_FMT[t]
        if f["bits"] == -1:
            bit_off = -1
            n = f["arr"]
            if t in ("fixstr", "fixstrW"):
                raw = data[pos: pos + tsize * n]
                val = raw.decode("shift_jis" if t == "fixstr" else "utf-16-le", errors="replace").split("\x00")[0]
            elif t == "dummy8":
                val = None
            else:
                vals = struct.unpack_from("<" + fmt * n, data, pos)
                val = vals[0] if n == 1 else list(vals)
            pos += tsize * n
        else:
            limit = BIT_LIMIT[t]
            tg = "u8" if t == "dummy8" else t  # dummy8 bit-padding shares the byte with u8 bits
            if bit_off == -1 or bit_type != tg or bit_off + f["bits"] > limit:
                bit_off, bit_type = 0, tg
                bit_val = struct.unpack_from("<" + fmt, data, pos)[0]
                pos += tsize
            val = (bit_val >> bit_off) & ((1 << f["bits"]) - 1)
            bit_off += f["bits"]
        if t != "dummy8":
            out[f["name"]] = val
    return out, pos


def load_names(def_name: str) -> dict[int, str]:
    p = PARAMDEX / "Names" / f"{def_name}.txt"
    names = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^\s*(-?\d+)\s+(.*)$", line)
            if m:
                names[int(m.group(1))] = m.group(2).strip()
    return names


class ERParam:
    def __init__(self, buf: bytes, def_name: str):
        self.ptype, self.fields = load_def(def_name, param_type_of(buf))
        self.names = load_names(def_name)
        strings_off = struct.unpack_from("<I", buf, 0)[0]
        self.row_count = struct.unpack_from("<H", buf, 0x0A)[0]
        flags2d = buf[0x2D]
        flags2e = buf[0x2E]
        long_offsets = bool(flags2d & 0x04)  # SoulsFormats: IntDataOffset=0x01, LongDataOffset=0x04, OffsetParamType=0x80
        off = 0x30
        if flags2d & 0x05:
            off += 0x10
        self.rows = {}
        self.row_order = []
        for i in range(self.row_count):
            if long_offsets:
                rid, _pad, doff, noff = struct.unpack_from("<iiQQ", buf, off)
                off += 24
            else:
                rid, doff, noff = struct.unpack_from("<iII", buf, off)
                off += 12
            self.rows[rid] = doff
            self.row_order.append(rid)
        self.buf = buf
        self.row_size = sum(field_size(f) for f in self.fields if f["bits"] == -1)  # approx (bitfields handled below)
        # exact row size: decode one row
        if self.row_order:
            _, self.row_size = decode_row(buf[self.rows[self.row_order[0]]:], self.fields)

    def __contains__(self, rid):
        return rid in self.rows

    def get(self, rid: int) -> dict:
        doff = self.rows[rid]
        vals, _ = decode_row(self.buf[doff: doff + self.row_size + 64], self.fields)
        vals["_id"] = rid
        vals["_name"] = self.names.get(rid, "")
        return vals

    def iter_rows(self):
        for rid in self.row_order:
            yield self.get(rid)


def open_regulation_params(bnd_path: Path, wanted: list[str]) -> dict[str, ERParam]:
    buf = bnd_path.read_bytes()
    _, entries = parse_bnd4(buf)
    out = {}
    for e in entries:
        name = e["name"].split("\\")[-1].removesuffix(".param")
        if name in wanted:
            out[name] = ERParam(buf[e["offset"]: e["offset"] + e["size"]], name)
    return out


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    params = open_regulation_params(Path(sys.argv[1]), [sys.argv[2]])
    p = params[sys.argv[2]]
    print(f"{sys.argv[2]}: type={p.ptype} rows={p.row_count} rowsize={p.row_size} fields={len(p.fields)}")
    for rid in [int(x) for x in sys.argv[3:]]:
        r = p.get(rid)
        print(f"== {rid} '{r['_name']}'")
        for k, v in r.items():
            if k.startswith("_"):
                continue
            if v not in (0, 0.0, -1, "", None, 255):
                print(f"   {k:<34} = {v}")
