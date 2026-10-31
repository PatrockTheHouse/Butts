"""DCX container: read any FromSoftware DCX (DFLT, KRAK, ZSTD) and write DFLT.

    import dcx
    raw = dcx.decompress(Path("wp_a_1503.partsbnd.dcx").read_bytes())     # bytes of the inner file
    kind = dcx.kind(buf)                                                    # b"DFLT" / b"KRAK" / b"ZSTD" / None
    out = dcx.compress_dflt(raw)                                            # DCX_DFLT_10000_44_9, accepted by ER
    out = dcx.compress_zstd(raw)                                            # DCX_ZSTD (ER 1.12+ regulation); Node zstd

    python tools/dcx.py IN [OUT]      # unwrap (OUT defaults to IN without .dcx); prints kind and sizes
    python tools/dcx.py --pack IN OUT # wrap as DFLT

KRAK (Oodle Kraken, Elden Ring's default) is decoded with the Linux build of ooz that vendor/setup.sh produces
(vendor/ooz/oozdec, see vendor/patches/ooz-linux.patch); it is decompression only, so KRAK sources are written
back as DFLT, which the game loads just as well. ZSTD (Elden Ring 1.12+ regulation) goes through Node's zlib.
"""
import os
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OOZDEC = Path(os.environ.get("MODKIT_OOZDEC", REPO_ROOT / "vendor" / "ooz" / "oozdec"))


def kind(buf: bytes):
    """Compression tag of a DCX buffer, or None if the buffer is not DCX."""
    if buf[:4] != b"DCX\x00":
        return None
    return bytes(buf[0x28:0x2C])


def _payload(buf: bytes):
    assert buf[:4] == b"DCX\x00", buf[:4]
    assert buf[0x18:0x1C] == b"DCS\x00", "DCX without DCS block"
    uncomp, comp = struct.unpack_from(">II", buf, 0x1C)
    dca = buf.index(b"DCA\x00", 0x28)
    dca_size = struct.unpack_from(">I", buf, dca + 4)[0]
    start = dca + dca_size
    return uncomp, comp, buf[start:start + comp]


def kraken_decompress(payload: bytes, uncompressed_size: int) -> bytes:
    if not OOZDEC.exists():
        raise RuntimeError(f"{OOZDEC} missing: run `bash vendor/setup.sh` (needs g++) to build the Oodle decoder")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "in.krak"
        dst = Path(td) / "out.bin"
        src.write_bytes(payload)
        r = subprocess.run([str(OOZDEC), str(src), str(dst), str(uncompressed_size)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"oozdec failed: {r.stderr.strip()}")
        return dst.read_bytes()


def zstd_decompress(payload: bytes) -> bytes:
    js = ("const z=require('zlib');const b=[];process.stdin.on('data',c=>b.push(c))"
          ".on('end',()=>process.stdout.write(z.zstdDecompressSync(Buffer.concat(b))))")
    return subprocess.run(["node", "-e", js], input=payload, capture_output=True, check=True).stdout


def decompress(buf: bytes) -> bytes:
    """Unwrap a DCX buffer of any supported kind; non-DCX input is returned unchanged."""
    k = kind(buf)
    if k is None:
        return buf
    uncomp, comp, payload = _payload(buf)
    if k == b"DFLT":
        out = zlib.decompress(payload)
    elif k == b"KRAK":
        out = kraken_decompress(payload, uncomp)
    elif k == b"ZSTD":
        out = zstd_decompress(payload)
    else:
        raise NotImplementedError(f"DCX compression {k!r}")
    assert len(out) == uncomp, (len(out), uncomp)
    return out


def compress_zstd(raw: bytes, level: int = 15) -> bytes:
    """DCX_ZSTD (Elden Ring 1.12+ regulation.bin style: DCP level byte 15). Needs Node >= 22.15 (zlib.zstdCompressSync)."""
    js = ("const z=require('zlib');const b=[];process.stdin.on('data',c=>b.push(c)).on('end',()=>process.stdout.write("
          f"z.zstdCompressSync(Buffer.concat(b),{{params:{{[z.constants.ZSTD_c_compressionLevel]:{int(level)}}}}})))")
    comp = subprocess.run(["node", "-e", js], input=raw, capture_output=True, check=True).stdout
    out = bytearray()
    out += b"DCX\x00" + struct.pack(">IIIII", 0x11000, 0x18, 0x24, 0x44, 0x4C)
    out += b"DCS\x00" + struct.pack(">II", len(raw), len(comp))
    out += b"DCP\x00" + b"ZSTD" + struct.pack(">IBBBBIIII", 0x20, int(level), 0, 0, 0, 0, 0, 0, 0x10100)
    out += b"DCA\x00" + struct.pack(">I", 8)
    out += comp
    return bytes(out)


def compress(raw: bytes, kind: bytes) -> bytes:
    """Wrap with the given DCX kind (b"DFLT" or b"ZSTD"; KRAK is written as DFLT, which the game accepts)."""
    if kind == b"ZSTD":
        return compress_zstd(raw)
    return compress_dflt(raw)


def compress_dflt(raw: bytes, level: int = 9) -> bytes:
    """DCX_DFLT_10000_44_9 (the format of ER's own c0000.anibnd.dcx)."""
    comp = zlib.compress(raw, level)
    out = bytearray()
    out += b"DCX\x00" + struct.pack(">IIIII", 0x11000, 0x18, 0x24, 0x44, 0x4C)
    out += b"DCS\x00" + struct.pack(">II", len(raw), len(comp))
    out += b"DCP\x00" + b"DFLT" + struct.pack(">IBBBBIIII", 0x20, 9, 0, 0, 0, 0, 0, 0, 0x10100)
    out += b"DCA\x00" + struct.pack(">I", 8)
    out += comp
    return bytes(out)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--pack":
        src, dst = Path(args[1]), Path(args[2])
        data = compress_dflt(src.read_bytes())
        dst.write_bytes(data)
        print(f"{dst}: DFLT {len(data)} bytes from {src.stat().st_size}")
        return
    src = Path(args[0])
    buf = src.read_bytes()
    k = kind(buf)
    if k is None:
        print(f"{src}: not DCX ({buf[:4]!r})")
        return
    out = decompress(buf)
    dst = Path(args[1]) if len(args) > 1 else src.with_suffix("") if src.suffix == ".dcx" else src.with_suffix(src.suffix + ".raw")
    dst.write_bytes(out)
    print(f"{src.name}: {k.decode()} {len(buf)} -> {len(out)} bytes ({out[:4]!r}) -> {dst}")


if __name__ == "__main__":
    main()
