"""List the files in a DS1 BND3 binder and summarize each PARAM inside it."""
import struct
import sys
from pathlib import Path


def read_cstr(buf: bytes, off: int, enc: str = "shift_jis") -> str:
    end = buf.index(b"\x00", off)
    return buf[off:end].decode(enc, errors="replace")


def parse_bnd3(buf: bytes):
    assert buf[:4] == b"BND3", buf[:4]
    version = buf[4:12].rstrip(b"\x00").decode()
    fmt = buf[12]
    big_endian = buf[13]
    count = struct.unpack_from("<i", buf, 0x10)[0]
    entries = []
    off = 0x20
    for _ in range(count):
        flags = buf[off]
        comp_size, data_off, file_id, name_off, uncomp_size = struct.unpack_from("<IIiII", buf, off + 4)
        name = read_cstr(buf, name_off)
        entries.append(dict(flags=flags, size=comp_size, offset=data_off, id=file_id, name=name, usize=uncomp_size))
        off += 24
    return version, fmt, big_endian, entries


def parse_param(buf: bytes):
    strings_off, data_off, unk06, pdef_ver, row_count = struct.unpack_from("<IHHHH", buf, 0)
    ptype = buf[0x0C:0x2C].split(b"\x00")[0].decode("ascii", errors="replace")
    endian, flags2a, flags2b, pdef_fmt = struct.unpack_from("<BBBB", buf, 0x2C)
    rows = []
    off = 0x30
    for _ in range(row_count):
        rid, roff, noff = struct.unpack_from("<iII", buf, off)
        name = read_cstr(buf, noff) if noff else ""
        rows.append((rid, roff, name))
        off += 12
    # row size: distance between consecutive row data offsets (or strings_off for last)
    row_size = None
    if row_count >= 2:
        row_size = rows[1][1] - rows[0][1]
    elif row_count == 1:
        row_size = strings_off - rows[0][1]
    return dict(type=ptype, pdef_ver=pdef_ver, rows=rows, row_size=row_size, data_off=data_off, strings_off=strings_off, flags=(flags2a, flags2b, pdef_fmt))


def main():
    path = Path(sys.argv[1])
    buf = path.read_bytes()
    version, fmt, be, entries = parse_bnd3(buf)
    print(f"BND3 version={version} format=0x{fmt:02x} big_endian={be} files={len(entries)}")
    for e in entries:
        pbuf = buf[e["offset"]: e["offset"] + e["size"]]
        p = parse_param(pbuf)
        named = sum(1 for r in p["rows"] if r[2])
        ids = [r[0] for r in p["rows"]]
        print(f"{e['id']:3d} {e['name']:<48} {e['size']:>8}  {p['type']:<28} rows={len(ids):5d} rowsize={p['row_size']} named={named} idrange={min(ids) if ids else None}-{max(ids) if ids else None}")


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    main()
