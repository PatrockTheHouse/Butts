"""Dump row IDs and names of one PARAM in a BND3 binder, optionally filtered."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bnd3_list import parse_bnd3, parse_param  # noqa: E402


def main():
    path = Path(sys.argv[1])
    want = sys.argv[2]
    pattern = re.compile(sys.argv[3]) if len(sys.argv) > 3 else None
    buf = path.read_bytes()
    _, _, _, entries = parse_bnd3(buf)
    for e in entries:
        if not e["name"].endswith("\\" + want + ".param"):
            continue
        p = parse_param(buf[e["offset"]: e["offset"] + e["size"]])
        print(f"# {want}: {len(p['rows'])} rows, row size {p['row_size']}")
        for rid, roff, name in p["rows"]:
            if pattern is None or pattern.search(name):
                print(f"{rid:>10}  {name}")


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    main()
