"""BND4 binder reader/writer (Elden Ring style, little-endian) with DCX (DFLT) wrapping.

    bnd = BND4.read(data)                     # data may be a DCX-wrapped binder
    bnd.files: list[BinderFile(id, name, data, flags)]
    bnd.set(id, name, data)                   # add or replace a file (kept sorted by id)
    out = bnd.write()                         # raw BND4 bytes
    dcx_dflt(out)                             # DCX/DFLT wrapper the game accepts

Layout written: 64-byte header, entry table, UTF-16 names, SoulsFormats-style path hash table (when the
source had one), file data in entry order padded to 16. Verified byte-identical on the vanilla c0000.anibnd.
"""
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path



def dcx_decompress(buf: bytes) -> bytes:
    """DCX (DFLT/zlib) unwrap; self-contained so this module runs outside the toolkit."""
    assert buf[:4] == b"DCX\x00", buf[:4]
    assert buf[0x18:0x1C] == b"DCS\x00"
    uncomp = struct.unpack_from(">I", buf, 0x1C)[0]
    kind = buf[0x28:0x2C]
    dca = buf.index(b"DCA\x00", 0x28)
    dca_size = struct.unpack_from(">I", buf, dca + 4)[0]
    if kind != b"DFLT":
        raise NotImplementedError(f"DCX compression {kind!r} not supported (only DFLT)")
    out = zlib.decompress(buf[dca + dca_size:])
    assert len(out) == uncomp, (len(out), uncomp)
    return out

FMT_IDS, FMT_NAMES1, FMT_NAMES2, FMT_LONG_OFFSETS, FMT_COMPRESSION = 0x02, 0x04, 0x08, 0x10, 0x20


def _reverse_bits(b: int) -> int:
    return int(f"{b:08b}"[::-1], 2)


@dataclass
class BinderFile:
    id: int
    name: str
    data: bytes
    flags: int = 0x40
    raw_pad: int = -1          # i32 after the flags (always -1 in game files)


@dataclass
class BND4:
    version: str = "07D7R6"
    unk04: int = 0
    unk05: int = 0
    big_endian: bool = False
    bit_big_endian: bool = True
    unicode: bool = True
    raw_format: int = 0x74                  # as stored (bit-reversed when bit_big_endian)
    extended: int = 4                       # 4 = has path hash table
    files: list = field(default_factory=list)

    # ---------------------------------------------------------------- format helpers
    @property
    def format(self) -> int:
        return _reverse_bits(self.raw_format) if self.bit_big_endian else self.raw_format

    def _layout(self):
        f = self.format
        has_ids = bool(f & FMT_IDS)
        has_names = bool(f & (FMT_NAMES1 | FMT_NAMES2))
        has_comp = bool(f & FMT_COMPRESSION)
        long_off = bool(f & FMT_LONG_OFFSETS)
        size = 8 + 8 + (8 if has_comp else 0) + (8 if long_off else 4) + (4 if has_ids else 0) + (4 if has_names else 0)
        return has_ids, has_names, has_comp, long_off, size

    # ---------------------------------------------------------------- read
    @classmethod
    def read(cls, data: bytes) -> "BND4":
        if data[:4] == b"DCX\x00":
            data = dcx_decompress(data)
        assert data[:4] == b"BND4", data[:4]
        self = cls()
        self.unk04, self.unk05 = data[4], data[5]
        self.big_endian = bool(data[9])
        self.bit_big_endian = bool(data[10])
        assert not self.big_endian, "big-endian BND4 not supported"
        count = struct.unpack_from("<i", data, 0x0C)[0]
        header_size = struct.unpack_from("<q", data, 0x10)[0]
        self.version = data[0x18:0x20].rstrip(b"\x00").decode()
        entry_size = struct.unpack_from("<q", data, 0x20)[0]
        self.unicode = bool(data[0x30])
        self.raw_format = data[0x31]
        self.extended = data[0x32]
        has_ids, has_names, has_comp, long_off, size = self._layout()
        assert size == entry_size, (size, entry_size, hex(self.raw_format))
        enc = "utf-16-le" if self.unicode else "shift_jis"
        off = header_size
        for i in range(count):
            p = off
            flags = data[p]
            raw_pad = struct.unpack_from("<i", data, p + 4)[0]
            p += 8
            comp = struct.unpack_from("<q", data, p)[0]
            p += 8
            usize = comp
            if has_comp:
                usize = struct.unpack_from("<q", data, p)[0]
                p += 8
            if long_off:
                doff = struct.unpack_from("<q", data, p)[0]
                p += 8
            else:
                doff = struct.unpack_from("<I", data, p)[0]
                p += 4
            fid = -1
            if has_ids:
                fid = struct.unpack_from("<i", data, p)[0]
                p += 4
            name = ""
            if has_names:
                noff = struct.unpack_from("<I", data, p)[0]
                p += 4
                if self.unicode:
                    end = noff
                    while data[end:end + 2] != b"\x00\x00":
                        end += 2
                    name = data[noff:end].decode("utf-16-le")
                else:
                    name = data[noff:data.index(b"\x00", noff)].decode("shift_jis")
            blob = bytes(data[doff:doff + comp])
            assert comp == usize, f"compressed binder entries not supported ({name})"
            self.files.append(BinderFile(fid, name, blob, flags, raw_pad))
            off += entry_size
        return self

    # ---------------------------------------------------------------- edit
    def get(self, name_or_id):
        for f in self.files:
            if f.id == name_or_id or f.name.split("\\")[-1].lower() == str(name_or_id).lower():
                return f
        return None

    def set(self, fid: int, name: str, data: bytes, flags: int = 0x40):
        for f in self.files:
            if f.id == fid:
                f.name, f.data, f.flags = name, data, flags
                return f
        nf = BinderFile(fid, name, data, flags)
        self.files.append(nf)
        self.files.sort(key=lambda f: f.id)
        return nf

    # ---------------------------------------------------------------- write
    def write(self) -> bytes:
        has_ids, has_names, has_comp, long_off, entry_size = self._layout()
        count = len(self.files)
        out = bytearray(64)
        entries_off = 64
        out += b"\x00" * (entry_size * count)
        # names
        name_offs = []
        for f in self.files:
            name_offs.append(len(out))
            out += f.name.encode("utf-16-le" if self.unicode else "shift_jis") + (b"\x00\x00" if self.unicode else b"\x00")
        hash_off = 0
        if self.extended == 4:
            while len(out) % 8:
                out.append(0)
            hash_off = len(out)
            out += self._hash_table(hash_off)
        data_off = len(out)
        # file data (entry order, 16-aligned)
        doffs = []
        for f in self.files:
            if f.data:                       # empty files sit wherever the cursor is (no padding)
                while len(out) % 16:
                    out.append(0)
            doffs.append(len(out))
            out += f.data
        # entries
        p = entries_off
        for f, noff, doff in zip(self.files, name_offs, doffs):
            struct.pack_into("<Bxxxi", out, p, f.flags, f.raw_pad)
            p += 8
            struct.pack_into("<q", out, p, len(f.data))
            p += 8
            if has_comp:
                struct.pack_into("<q", out, p, len(f.data))
                p += 8
            if long_off:
                struct.pack_into("<q", out, p, doff)
                p += 8
            else:
                struct.pack_into("<I", out, p, doff)
                p += 4
            if has_ids:
                struct.pack_into("<i", out, p, f.id)
                p += 4
            if has_names:
                struct.pack_into("<I", out, p, noff)
                p += 4
        # header
        hdr = struct.pack("<4sBBBBBBBBi", b"BND4", self.unk04, self.unk05, 0, 0, 0, int(self.big_endian),
                          int(self.bit_big_endian), 0, count)
        hdr += struct.pack("<q8sqq", 64, self.version.encode().ljust(8, b"\x00"), entry_size, data_off)
        hdr += struct.pack("<BBBBiq", int(self.unicode), self.raw_format, self.extended, 0, 0, hash_off)
        assert len(hdr) == 64
        out[:64] = hdr
        return bytes(out)

    def _hash_table(self, table_off: int) -> bytes:
        n = len(self.files)
        groups = max(n // 7, 1)
        while not _is_prime(groups):
            groups += 1
        lists = [[] for _ in range(groups)]
        for i, f in enumerate(self.files):
            h = path_hash(f.name)
            lists[h % groups].append((h, i))
        for lst in lists:
            lst.sort(key=lambda t: t[0])
        buckets = bytearray()
        hashes = bytearray()
        idx = 0
        for lst in lists:
            buckets += struct.pack("<ii", len(lst), idx)
            for h, i in lst:
                hashes += struct.pack("<Ii", h, i)
                idx += 1
        hashes_off = table_off + 16 + len(buckets)
        return struct.pack("<qII", hashes_off, groups, 0x00080810) + bytes(buckets) + bytes(hashes)


def path_hash(path: str) -> int:
    s = path.lower().replace("\\", "/")
    if not s.startswith("/"):
        s = "/" + s
    h = 0
    for c in s:
        h = (h * 37 + ord(c)) & 0xFFFFFFFF
    return h


def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    i = 2
    while i * i <= n:
        if n % i == 0:
            return False
        i += 1
    return True


def dcx_dflt(raw: bytes, level: int = 9) -> bytes:
    comp = zlib.compress(raw, level)
    hdr = struct.pack(">4sIIIII", b"DCX\x00", 0x11000, 0x18, 0x24, 0x44, 0x4C)
    hdr += struct.pack(">4sII", b"DCS\x00", len(raw), len(comp))
    hdr += struct.pack(">4s4sIIIIII", b"DCP\x00", b"DFLT", 0x20, 0x09000000, 0, 0, 0, 0x00010100)
    hdr += struct.pack(">4sI", b"DCA\x00", 8)
    assert len(hdr) == 0x4C
    return hdr + comp


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    src = Path(sys.argv[1])
    raw = src.read_bytes()
    plain = dcx_decompress(raw) if raw[:4] == b"DCX\x00" else raw
    bnd = BND4.read(plain)
    out = bnd.write()
    if out == plain:
        print(f"{src.name}: BND4 round trip byte-identical ({len(out)} bytes, {len(bnd.files)} files)")
    else:
        n = min(len(out), len(plain))
        first = next((i for i in range(n) if out[i] != plain[i]), n)
        print(f"{src.name}: DIFF at {first:#x} sizes {len(plain)} vs {len(out)}: orig={plain[first:first+16].hex(' ')} new={out[first:first+16].hex(' ')}")
    if raw[:4] == b"DCX\x00":
        rd = dcx_dflt(out)
        print(f"  DCX: original {len(raw)} bytes, rewritten {len(rd)} bytes, {'identical' if rd == raw else 'different stream (decompresses identically: %s)' % (dcx_decompress(rd) == plain)}")
