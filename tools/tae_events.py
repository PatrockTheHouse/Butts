"""Elden Ring TAE event payload encode/decode using the DSAnimStudio ER template (field names and types).

    fields(ev_type)                         -> [(tag, name, size), ...]
    decode(ev_type, payload)                -> {name_or_@off: value}
    encode(ev_type, base=None, **values)    -> bytes  (start from `base` payload or zeros, set named fields)
    size(ev_type)                           -> template payload size

Unnamed template fields are addressed as "@<offset>" (the same keys decode() produces).
"""
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import modkit_env  # noqa: E402

FMT = {"s8": "b", "u8": "B", "x8": "B", "b": "B", "s16": "h", "u16": "H", "x16": "H", "s32": "i", "u32": "I",
       "x32": "I", "f32": "f", "f32grad": "f", "s64": "q", "u64": "Q", "x64": "Q", "f64": "d"}
_TEMPLATE: dict[int, tuple[str, list]] | None = None


def template() -> dict[int, tuple[str, list]]:
    global _TEMPLATE
    if _TEMPLATE is None:
        root = ET.parse(modkit_env.tae_template("ER")).getroot()
        _TEMPLATE = {}
        for act in root.findall("action"):
            flds = []
            for f in act:
                if f.tag == "aob":
                    flds.append(("aob", "", int(f.get("length", "0"))))
                else:
                    flds.append((f.tag, f.get("name", ""), struct.calcsize(FMT[f.tag])))
            _TEMPLATE[int(act.get("id"))] = (act.get("name"), flds)
    return _TEMPLATE


def name(ev_type: int) -> str:
    t = template().get(ev_type)
    return t[0] if t else f"type{ev_type}"


def fields(ev_type: int) -> list:
    return template()[ev_type][1]


def size(ev_type: int) -> int:
    return sum(f[2] for f in fields(ev_type))


def _layout(ev_type: int):
    """[(key, tag, offset, size)] with key = field name or '@off'."""
    out = []
    off = 0
    for tag, fname, sz in fields(ev_type):
        key = fname or f"@{off}"
        out.append((key, tag, off, sz))
        off += sz
    return out


def decode(ev_type: int, payload: bytes) -> dict:
    out = {}
    for key, tag, off, sz in _layout(ev_type):
        if off + sz > len(payload):
            break
        if tag == "aob":
            out[key] = payload[off:off + sz]
        else:
            out[key] = struct.unpack_from("<" + FMT[tag], payload, off)[0]
    return out


def encode(ev_type: int, base: bytes | None = None, **values) -> bytes:
    buf = bytearray(base if base is not None else bytes(size(ev_type)))
    if len(buf) < size(ev_type):
        buf.extend(bytes(size(ev_type) - len(buf)))
    layout = {k: (tag, off, sz) for k, tag, off, sz in _layout(ev_type)}
    for k, v in values.items():
        if k not in layout:
            raise KeyError(f"event {ev_type} ({name(ev_type)}) has no field {k!r}; fields: {list(layout)}")
        tag, off, sz = layout[k]
        if tag == "aob":
            buf[off:off + sz] = bytes(v).ljust(sz, b"\0")[:sz]
        else:
            struct.pack_into("<" + FMT[tag], buf, off, v)
    return bytes(buf)


if __name__ == "__main__":
    for t in (0, 2, 16, 34, 35, 66, 67, 96, 119, 129, 224, 225, 238, 605, 700):
        print(t, name(t), size(t), [k for k, *_ in _layout(t)])
