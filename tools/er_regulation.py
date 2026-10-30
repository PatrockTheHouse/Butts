"""Decrypt + decompress Elden Ring's regulation.bin into a raw BND4 (and list its params)."""
import struct
import subprocess
import sys
import zlib
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

sys.path.insert(0, str(Path(__file__).parent))
from bnd_tools import parse_bnd4  # noqa: E402

ER_KEY = bytes.fromhex("99bffc366a6bc8c6f5827d093602d676c42892a01c207fb024d3af4e493fef99")


def decrypt_regulation(data: bytes) -> bytes:
    iv, body = data[:16], data[16:]
    dec = Cipher(algorithms.AES(ER_KEY), modes.CBC(iv)).decryptor()
    return dec.update(body) + dec.finalize()


def zstd_decompress(data: bytes) -> bytes:
    js = "const z=require('zlib');const b=[];process.stdin.on('data',c=>b.push(c)).on('end',()=>process.stdout.write(z.zstdDecompressSync(Buffer.concat(b))))"
    return subprocess.run(["node", "-e", js], input=data, capture_output=True, check=True).stdout


def dcx_decompress_any(buf: bytes) -> bytes:
    assert buf[:4] == b"DCX\x00", buf[:16]
    uncomp, comp = struct.unpack_from(">II", buf, 0x1C)
    kind = buf[0x28:0x2C]
    dca = buf.index(b"DCA\x00", 0x28)
    dca_size = struct.unpack_from(">I", buf, dca + 4)[0]
    payload = buf[dca + dca_size:]
    if kind == b"DFLT":
        out = zlib.decompress(payload)
    elif kind == b"ZSTD":
        out = zstd_decompress(payload)
    else:
        raise NotImplementedError(kind)
    assert len(out) == uncomp, (len(out), uncomp)
    return out, kind


def main():
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    dec = decrypt_regulation(src.read_bytes())
    print("decrypted header:", dec[:4], "DCP:", dec[0x28:0x2C])
    bnd, kind = dcx_decompress_any(dec)
    dst.write_bytes(bnd)
    hdr, entries = parse_bnd4(bnd)
    print(f"{kind.decode()} -> {len(bnd)} bytes; {hdr['kind']} {hdr['version']} with {len(entries)} files")
    for e in entries[:8]:
        print("  ", e["id"], e["size"], e["name"])
    print("   ...")


if __name__ == "__main__":
    main()
