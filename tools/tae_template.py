"""Summarize a DSAnimStudio TAE template: action id, name, and field list (type:name)."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

TYPE_SIZES = {"s8": 1, "u8": 1, "x8": 1, "b": 1, "s16": 2, "u16": 2, "x16": 2, "s32": 4, "u32": 4, "x32": 4,
              "f32": 4, "s64": 8, "u64": 8, "x64": 8, "f64": 8, "aob": None}


def summarize(path, only=None):
    root = ET.parse(path).getroot()
    print(f"# {root.get('game')} template: {len(root)} actions")
    for act in root.findall("action"):
        aid = int(act.get("id"))
        if only and aid not in only:
            continue
        fields = []
        size = 0
        for f in act:
            t = f.tag
            n = f.get("name", "")
            if t == "aob":
                ln = int(f.get("length", "0"))
                fields.append(f"pad[{ln}]")
                size += ln
            else:
                fields.append(f"{t}:{n}")
                size += TYPE_SIZES.get(t, 0) or 0
        print(f"{aid:>4} {act.get('name'):<44} ({size:>2}B) " + ", ".join(fields))


if __name__ == "__main__":
    arg = sys.argv[1]
    if not arg.endswith(".xml"):
        import modkit_env
        arg = modkit_env.tae_template(arg)
    only = set(int(x) for x in sys.argv[2:]) or None
    summarize(arg, only)
