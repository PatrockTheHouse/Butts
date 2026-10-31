"""DDS texture helpers without texconv: decode any DDS to RGBA (Pillow), encode RGBA to DXT1/DXT5/BC5 with full
mip chains (Pillow's BC encoders), and rewrite legacy DDS headers as DX10 (sRGB/linear) or back.

    import dds_tools as D
    rgba = D.decode(dds_bytes)                      # (H, W, 4) uint8
    dds  = D.encode(rgba, "DXT5", mipmaps=True)      # legacy-header DDS, mips down to 1x1
    dds  = D.to_dx10(dds, srgb=True)                 # same payload with a DX10 header (BC1/BC3_UNORM[_SRGB], BC5_UNORM)
    info = D.header_info(dds)                        # dict(width, height, mipmaps, fourcc, dxgi_format, block_bytes)

    python tools/dds_tools.py info FILE.dds
    python tools/dds_tools.py png FILE.dds OUT.png

Only block-compressed formats are handled for encoding (BC1 = DXT1, BC3 = DXT5, BC5 = ATI2); decoding covers
whatever Pillow's DDS plugin reads (DXT1/3/5, BC4/5/6/7, uncompressed). BC7 encoding is not available here.
"""
import io
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

DDS_MAGIC = b"DDS "
DDSD_CAPS, DDSD_HEIGHT, DDSD_WIDTH, DDSD_PITCH, DDSD_PIXELFORMAT, DDSD_MIPMAPCOUNT, DDSD_LINEARSIZE = (
    0x1, 0x2, 0x4, 0x8, 0x1000, 0x20000, 0x80000)
DDPF_FOURCC = 0x4
DDSCAPS_COMPLEX, DDSCAPS_TEXTURE, DDSCAPS_MIPMAP = 0x8, 0x1000, 0x400000

# fourcc -> (bytes per 4x4 block, DXGI linear, DXGI sRGB)
BLOCK_FORMATS = {
    b"DXT1": (8, 71, 72),      # BC1_UNORM / BC1_UNORM_SRGB
    b"DXT3": (16, 74, 75),     # BC2
    b"DXT5": (16, 77, 78),     # BC3
    b"ATI1": (8, 80, None),    # BC4_UNORM
    b"BC4U": (8, 80, None),
    b"ATI2": (16, 83, None),   # BC5_UNORM
    b"BC5U": (16, 83, None),
}
DXGI_BLOCK_BYTES = {71: 8, 72: 8, 74: 16, 75: 16, 77: 16, 78: 16, 80: 8, 83: 16, 95: 16, 96: 16, 98: 16, 99: 16}
DXGI_NAMES = {71: "BC1_UNORM", 72: "BC1_UNORM_SRGB", 74: "BC2_UNORM", 75: "BC2_UNORM_SRGB", 77: "BC3_UNORM",
              78: "BC3_UNORM_SRGB", 80: "BC4_UNORM", 83: "BC5_UNORM", 95: "BC6H_UF16", 98: "BC7_UNORM",
              99: "BC7_UNORM_SRGB", 28: "R8G8B8A8_UNORM", 29: "R8G8B8A8_UNORM_SRGB"}


def header_info(dds: bytes) -> dict:
    assert dds[:4] == DDS_MAGIC, dds[:4]
    size, flags, height, width, pitch, depth, mips = struct.unpack_from("<7I", dds, 4)
    pf_size, pf_flags, fourcc, rgb_bits = struct.unpack_from("<III", dds, 0x4C) + (struct.unpack_from("<I", dds, 0x58)[0],)
    fourcc = dds[0x54:0x58]
    info = {"width": width, "height": height, "mipmaps": mips or 1, "fourcc": fourcc, "flags": flags, "dxgi_format": None,
            "data_offset": 128, "pf_flags": pf_flags, "rgb_bits": rgb_bits}
    if fourcc == b"DX10":
        dxgi, dim, misc, arr, misc2 = struct.unpack_from("<5I", dds, 128)
        info.update(dxgi_format=dxgi, data_offset=148, array_size=arr, misc2=misc2)
        info["block_bytes"] = DXGI_BLOCK_BYTES.get(dxgi)
    elif fourcc in BLOCK_FORMATS:
        info["block_bytes"] = BLOCK_FORMATS[fourcc][0]
    else:
        info["block_bytes"] = None
    return info


def decode(dds: bytes) -> np.ndarray:
    """Top mip level as (H, W, 4) uint8 RGBA. DX10 BC1-5 headers (incl. sRGB variants Pillow lacks) are read
    through their legacy fourcc form; BC6/BC7 go straight to Pillow."""
    try:
        im = Image.open(io.BytesIO(dds))
        im.load()
    except NotImplementedError:
        info = header_info(dds)
        if info["fourcc"] != b"DX10" or info["dxgi_format"] not in (71, 72, 74, 75, 77, 78, 80, 83):
            raise
        im = Image.open(io.BytesIO(to_legacy(dds)))
        im.load()
    return np.asarray(im.convert("RGBA"))


def _encode_level(rgba: np.ndarray, fmt: str) -> bytes:
    """Encode one level with Pillow and strip its 128-byte legacy header."""
    mode_img = Image.fromarray(rgba, "RGBA")
    if fmt == "BC5":
        mode_img = mode_img.convert("RGB")
    buf = io.BytesIO()
    mode_img.save(buf, format="DDS", pixel_format=fmt)
    data = buf.getvalue()
    assert data[:4] == DDS_MAGIC
    return data[128:]


def mip_chain(rgba: np.ndarray, mipmaps: bool = True) -> list[np.ndarray]:
    levels = [rgba]
    if not mipmaps:
        return levels
    im = Image.fromarray(rgba, "RGBA")
    w, h = im.size
    while w > 1 or h > 1:
        w, h = max(1, w // 2), max(1, h // 2)
        im = im.resize((w, h), Image.Resampling.LANCZOS)
        levels.append(np.asarray(im))
    return levels


def encode(rgba: np.ndarray, fmt: str = "DXT5", mipmaps: bool = True) -> bytes:
    """RGBA (H, W, 4) uint8 -> legacy-header DDS (fourcc DXT1/DXT5/ATI2) with a full mip chain."""
    fourcc = {"DXT1": b"DXT1", "BC1": b"DXT1", "DXT5": b"DXT5", "BC3": b"DXT5", "BC5": b"ATI2", "ATI2": b"ATI2"}[fmt]
    pil_fmt = {b"DXT1": "DXT1", b"DXT5": "DXT5", b"ATI2": "BC5"}[fourcc]
    levels = mip_chain(np.ascontiguousarray(rgba, dtype=np.uint8), mipmaps)
    payload = b"".join(_encode_level(lv, pil_fmt) for lv in levels)
    h, w = rgba.shape[:2]
    block = BLOCK_FORMATS[fourcc][0]
    linear_size = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_LINEARSIZE
    caps = DDSCAPS_TEXTURE
    if len(levels) > 1:
        flags |= DDSD_MIPMAPCOUNT
        caps |= DDSCAPS_COMPLEX | DDSCAPS_MIPMAP
    hdr = struct.pack("<4s7I", DDS_MAGIC, 124, flags, h, w, linear_size, 0, len(levels)) + b"\0" * 44
    hdr += struct.pack("<II4s5I", 32, DDPF_FOURCC, fourcc, 0, 0, 0, 0, 0)
    hdr += struct.pack("<5I", caps, 0, 0, 0, 0)
    assert len(hdr) == 128
    return hdr + payload


def to_dx10(dds: bytes, srgb: bool = False) -> bytes:
    """Rewrite a legacy block-compressed DDS header as DX10 (BC1/BC3/BC5 DXGI formats). sRGB only for BC1/2/3."""
    info = header_info(dds)
    if info["fourcc"] == b"DX10":
        return dds
    block, lin, srgb_fmt = BLOCK_FORMATS[info["fourcc"]]
    dxgi = srgb_fmt if srgb else lin
    if dxgi is None:
        raise ValueError(f"{info['fourcc']!r} has no sRGB DXGI variant")
    hdr = bytearray(dds[:128])
    hdr[0x54:0x58] = b"DX10"
    dx10 = struct.pack("<5I", dxgi, 3, 0, 1, 0)   # D3D10_RESOURCE_DIMENSION_TEXTURE2D, no misc flags, array 1, alpha unknown
    return bytes(hdr) + dx10 + dds[128:]


def to_legacy(dds: bytes) -> bytes:
    """Rewrite a DX10 BC1/BC2/BC3/BC4/BC5 header as the legacy fourcc form (BC7 cannot be expressed that way)."""
    info = header_info(dds)
    if info["fourcc"] != b"DX10":
        return dds
    fourcc = {71: b"DXT1", 72: b"DXT1", 74: b"DXT3", 75: b"DXT3", 77: b"DXT5", 78: b"DXT5", 80: b"ATI1", 83: b"ATI2"}.get(info["dxgi_format"])
    if fourcc is None:
        raise ValueError(f"DXGI format {info['dxgi_format']} has no legacy fourcc")
    hdr = bytearray(dds[:128])
    hdr[0x54:0x58] = fourcc
    return bytes(hdr) + dds[148:]


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    cmd, path = sys.argv[1], Path(sys.argv[2])
    dds = path.read_bytes()
    info = header_info(dds)
    if cmd == "info":
        name = DXGI_NAMES.get(info["dxgi_format"], info["dxgi_format"]) if info["fourcc"] == b"DX10" else info["fourcc"]
        print(f"{path.name}: {info['width']}x{info['height']} mips {info['mipmaps']} {name} data {len(dds) - info['data_offset']} bytes")
    elif cmd == "png":
        Image.fromarray(decode(dds), "RGBA").save(sys.argv[3])
        print(f"wrote {sys.argv[3]}")


if __name__ == "__main__":
    main()
