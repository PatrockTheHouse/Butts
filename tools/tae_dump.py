"""Human-readable dump of an Elden Ring TAE using the DSAnimStudio event template for field names.

    tae_dump.py FILE.tae [ANIM_ID ...]        # all animations, or only the listed ids
    tae_dump.py FILE.tae --types 2,96,128     # only events of these types
"""
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import modkit_env  # noqa: E402
from tae3 import read_tae  # noqa: E402

FMT = {"s8": "b", "u8": "B", "x8": "B", "b": "B", "s16": "h", "u16": "H", "x16": "H", "s32": "i", "u32": "I",
       "x32": "I", "f32": "f", "f32grad": "f", "s64": "q", "u64": "Q", "x64": "Q", "f64": "d"}
SIZE = {k: struct.calcsize(v) for k, v in FMT.items()}

_TEMPLATE = None


def template():
    global _TEMPLATE
    if _TEMPLATE is None:
        root = ET.parse(modkit_env.tae_template("ER")).getroot()
        _TEMPLATE = {}
        for act in root.findall("action"):
            fields = []
            for f in act:
                if f.tag == "aob":
                    fields.append(("aob", "", int(f.get("length", "0"))))
                else:
                    fields.append((f.tag, f.get("name", ""), SIZE[f.tag]))
            _TEMPLATE[int(act.get("id"))] = (act.get("name"), fields)
    return _TEMPLATE


def decode(ev_type: int, payload: bytes) -> tuple[str, str]:
    """Return (event name, 'field=value ...') using the template; raw ints as fallback."""
    t = template().get(ev_type)
    if t is None:
        return f"type{ev_type}", raw(payload)
    name, fields = t
    out = []
    off = 0
    for tag, fname, size in fields:
        if off + size > len(payload):
            break
        if tag == "aob":
            chunk = payload[off:off + size]
            if any(chunk):
                out.append(f"pad={chunk.hex()}")
        else:
            v = struct.unpack_from("<" + FMT[tag], payload, off)[0]
            if tag.startswith("f32"):
                v = f"{v:g}"
            label = fname or f"@{off}"
            if not (fname == "" and v == 0):
                out.append(f"{label}={v}")
        off += size
    if off < len(payload) and any(payload[off:]):
        out.append(f"extra={payload[off:].hex()}")
    return name, " ".join(out)


def raw(payload: bytes) -> str:
    n = len(payload) // 4
    return " ".join(str(v) for v in struct.unpack_from("<" + "i" * n, payload, 0))


def dump(path: Path, only_ids=None, only_types=None):
    tae = read_tae(path.read_bytes())
    print(f"TAE {tae.tae_id} ({path.name}) skel={tae.skeleton_name} animations={len(tae.animations)}")
    for a in tae.animations:
        if only_ids and a.id not in only_ids:
            continue
        ref = f" REF->{a.file_a}" if a.file_type == 1 else (f" IMPORT {a.file_b}" if a.file_a & 0x100 else "")
        print(f"  anim {a.id} file={a.file_name!r}{ref} events={len(a.events)} groups={len(a.groups)}")
        grp_of = {}
        for gi, g in enumerate(a.groups):
            for idx in g.event_indices:
                grp_of[idx] = g.type
        for i, e in enumerate(a.events):
            if only_types and e.type not in only_types:
                continue
            name, fields = decode(e.type, e.payload)
            g = grp_of.get(i, "-")
            print(f"    {e.start:6.3f}-{e.end:6.3f} [{e.type:>3} {name:<28}] g={g:<4} {fields}")


def main():
    args = sys.argv[1:]
    only_types = None
    if "--types" in args:
        k = args.index("--types")
        only_types = {int(x) for x in args[k + 1].split(",")}
        del args[k:k + 2]
    path = Path(args[0])
    ids = {int(x) for x in args[1:]} or None
    dump(path, ids, only_types)


if __name__ == "__main__":
    main()
