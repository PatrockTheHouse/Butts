"""Minimal readers for FromSoft DCX (any kind, via dcx.py), BND3 and BND4 binders."""
import struct
import sys
import zlib
from pathlib import Path


def dcx_decompress(buf: bytes) -> bytes:
    assert buf[:4] == b"DCX\x00", buf[:4]
    assert buf[0x18:0x1C] == b"DCS\x00"
    uncomp, comp = struct.unpack_from(">II", buf, 0x1C)
    assert buf[0x24:0x28] == b"DCP\x00"
    kind = buf[0x28:0x2C]
    dca_off = buf.index(b"DCA\x00", 0x28)
    dca_size = struct.unpack_from(">I", buf, dca_off + 4)[0]
    data = buf[dca_off + dca_size:]
    if kind == b"DFLT":
        out = zlib.decompress(data)
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import dcx                             # KRAK via vendor/ooz/oozdec, ZSTD via Node
        out = dcx.decompress(buf)
    assert len(out) == uncomp, (len(out), uncomp)
    return out


def _cstr(buf: bytes, off: int, enc: str) -> str:
    if enc.startswith("utf-16"):
        end = off
        while buf[end:end + 2] != b"\x00\x00":
            end += 2
        return buf[off:end].decode(enc, errors="replace")
    end = buf.index(b"\x00", off)
    return buf[off:end].decode(enc, errors="replace")


def parse_bnd3(buf: bytes):
    assert buf[:4] == b"BND3"
    version = buf[4:12].rstrip(b"\x00").decode()
    fmt = buf[12]
    count = struct.unpack_from("<i", buf, 0x10)[0]
    entries = []
    off = 0x20
    for _ in range(count):
        flags = buf[off]
        comp_size, data_off, file_id, name_off, uncomp_size = struct.unpack_from("<IIiII", buf, off + 4)
        entries.append(dict(flags=flags, size=comp_size, offset=data_off, id=file_id,
                            name=_cstr(buf, name_off, "shift_jis"), usize=uncomp_size))
        off += 24
    return dict(kind="BND3", version=version, fmt=fmt), entries


def parse_bnd4(buf: bytes):
    assert buf[:4] == b"BND4"
    unk04, unk05, big_endian, bit_big_endian = buf[4], buf[5], buf[9], buf[10]
    bo = ">" if big_endian else "<"
    count = struct.unpack_from(bo + "i", buf, 0x0C)[0]
    header_size = struct.unpack_from(bo + "q", buf, 0x10)[0]
    version = buf[0x18:0x20].rstrip(b"\x00").decode()
    entry_size = struct.unpack_from(bo + "q", buf, 0x20)[0]
    data_off = struct.unpack_from(bo + "q", buf, 0x28)[0]
    unicode = buf[0x30]
    fmt = buf[0x31]
    extended = buf[0x32]
    enc = "utf-16-le" if unicode else "shift_jis"
    # Binder.Format flags (SoulsFormats): IDs=0x02 Names1=0x04 Names2=0x08 LongOffsets=0x10 Compression=0x20.
    # The raw byte may be stored bit-reversed; pick the interpretation whose entry size matches the header.
    def layout(raw):
        has_ids = bool(raw & 0x02)
        has_names = bool(raw & 0x04) or bool(raw & 0x08)
        has_comp = bool(raw & 0x20)
        long_offsets = bool(raw & 0x10)
        size = 8 + 8 + (8 if has_comp else 0) + (8 if long_offsets else 4) + (4 if has_ids else 0) + (4 if has_names else 0)
        return has_ids, has_names, has_comp, long_offsets, size

    reversed_fmt = int(f"{fmt:08b}"[::-1], 2)
    for raw in (reversed_fmt, fmt):
        has_ids, has_names, has_comp, long_offsets, size = layout(raw)
        if size == entry_size:
            break
    else:
        raise ValueError(f"cannot match BND4 entry size {entry_size} for format 0x{fmt:02x}")
    entries = []
    off = header_size
    for _ in range(count):
        p = off
        eflags = buf[p]
        p += 8
        comp_size = struct.unpack_from(bo + "q", buf, p)[0]
        p += 8
        usize = comp_size
        if has_comp:
            usize = struct.unpack_from(bo + "q", buf, p)[0]
            p += 8
        if long_offsets:
            doff = struct.unpack_from(bo + "q", buf, p)[0]
            p += 8
        else:
            doff = struct.unpack_from(bo + "I", buf, p)[0]
            p += 4
        fid = -1
        if has_ids:
            fid = struct.unpack_from(bo + "i", buf, p)[0]
            p += 4
        name = ""
        if has_names:
            noff = struct.unpack_from(bo + "I", buf, p)[0]
            p += 4
            name = _cstr(buf, noff, enc)
        entries.append(dict(flags=eflags, size=comp_size, usize=usize, offset=doff, id=fid, name=name))
        off += entry_size
    return dict(kind="BND4", version=version, fmt=fmt, raw_fmt=raw, entry_size=entry_size, unicode=unicode), entries


def parse_binder(buf: bytes):
    if buf[:4] == b"DCX\x00":
        buf = dcx_decompress(buf)
    if buf[:4] == b"BND3":
        hdr, entries = parse_bnd3(buf)
    elif buf[:4] == b"BND4":
        hdr, entries = parse_bnd4(buf)
    else:
        raise ValueError(buf[:4])
    return buf, hdr, entries


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    path = Path(sys.argv[1])
    buf, hdr, entries = parse_binder(path.read_bytes())
    print(hdr, "files:", len(entries), "decompressed:", len(buf))
    for e in entries:
        magic = buf[e["offset"]: e["offset"] + 8]
        print(f"{e['id']:>6} {e['size']:>9} {magic!r:<22} {e['name']}")
