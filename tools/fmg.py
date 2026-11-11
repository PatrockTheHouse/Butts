"""FMG text tables (FromSoft message files) - the version 2 / 64-bit layout of DS3, Sekiro and Elden Ring.

    import fmg
    t = fmg.FMG.read(data)            # t.entries: {id: str}, t.groups: [(first_id, last_id)] as stored
    t.entries[43200000] = "SMG"       # new ids get their own groups (consecutive ids merged); existing ids keep theirs
    data = t.write()                  # byte-identical to the input when nothing changed

Layout (little-endian): 0, bigEndian (0), version (2), 0 | u32 fileSize | 1, unk09, 0, 0 | u32 groupCount |
u32 stringCount | u32 0xFF | u64 stringOffsetsOffset | u64 0 | groups[groupCount] = (u32 firstStringIndex,
u32 firstId, u32 lastId, u32 0) | u64 stringOffsets[stringCount] (0 = empty/null string) | UTF-16LE strings,
NUL-terminated. Ids in a group are consecutive; a null offset means "no text" for that id.
"""
import struct


class FMG:
    def __init__(self):
        self.version = 2
        self.unk09 = 0
        self.entries: dict[int, str | None] = {}
        self.groups: list[tuple[int, int]] = []   # (first_id, last_id) in file order

    @classmethod
    def read(cls, data: bytes) -> "FMG":
        self = cls()
        z0, big, ver, z1, size, one, unk9, z2, z3, n_groups, n_strings = struct.unpack_from("<BBBBIBBBBII", data, 0)
        if big or ver != 2 or one != 1:
            raise ValueError(f"not a little-endian version-2 FMG (bigEndian {big}, version {ver})")
        self.unk09 = unk9
        ff, soff, z4 = struct.unpack_from("<IQQ", data, 0x14)
        if ff != 0xFF:
            raise ValueError("expected the 0xFF wide-offset marker")
        pos = 0x28
        offsets = struct.unpack_from(f"<{n_strings}Q", data, soff)
        for g in range(n_groups):
            first_idx, first_id, last_id, _ = struct.unpack_from("<IIII", data, pos + 16 * g)
            self.groups.append((first_id, last_id))
            for k, i in enumerate(range(first_id, last_id + 1)):
                o = offsets[first_idx + k]
                if o == 0:
                    self.entries[i] = None
                else:
                    end = o
                    while data[end:end + 2] != b"\0\0":
                        end += 2
                    self.entries[i] = data[o:end].decode("utf-16-le")
        return self

    def write(self) -> bytes:
        # existing groups keep their order; ids outside any stored group are appended as new (merged) groups
        covered = set()
        for a, b in self.groups:
            covered.update(range(a, b + 1))
        extra = sorted(i for i in self.entries if i not in covered)
        groups = list(self.groups)
        k = 0
        while k < len(extra):
            j = k
            while j + 1 < len(extra) and extra[j + 1] == extra[j] + 1:
                j += 1
            groups.append((extra[k], extra[j]))
            k = j + 1
        ids = [i for a, b in groups for i in range(a, b + 1)]
        n_groups, n_strings = len(groups), len(ids)
        header_size = 0x28
        groups_size = 16 * n_groups
        soff = header_size + groups_size
        strings_start = soff + 8 * n_strings
        strings = bytearray()
        offsets = []
        for i in ids:
            s = self.entries.get(i)
            if s is None:
                offsets.append(0)
            else:
                offsets.append(strings_start + len(strings))
                strings += s.encode("utf-16-le") + b"\0\0"
        size = strings_start + len(strings)
        out = bytearray()
        out += struct.pack("<BBBBIBBBBII", 0, 0, 2, 0, size, 1, self.unk09, 0, 0, n_groups, n_strings)
        out += struct.pack("<IQQ", 0xFF, soff, 0)
        idx = 0
        for a, b in groups:
            out += struct.pack("<IIII", idx, a, b, 0)
            idx += b - a + 1
        out += struct.pack(f"<{n_strings}Q", *offsets)
        out += strings
        return bytes(out)


def round_trip_ok(data: bytes) -> bool:
    return FMG.read(data).write() == data
