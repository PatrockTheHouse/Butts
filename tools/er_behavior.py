"""Elden Ring behavior graph (c0000.behbnd.dcx -> Behaviors/c0000.hkx) surgery: register the animations of a new TAE
category so the engine can select them.

    import er_behavior
    new_beh, report = er_behavior.register_categories(beh_bytes, base_cat=46, new_cats=[974, 975], slots={37110, ...})
    er_behavior.validate(new_beh)                         # pointer / item sanity
    er_behavior.children_report(new_beh, "a046_037110")   # which selectors own the clip, and their children

    python er_behavior.py c0000_beh.hkx a046_037110       # same report from the command line

Why this is needed (learned from the player's graph: hk2018 tagfile, 30k clip generators, 4k selectors):
  * Every animation state ends in a `CustomManualSelectorGenerator` (CMSG) carrying `animId` (37110 = crossbow raise)
    and `offsetType` (13 right-hand weapon category, 14 left hand, 16 hand style + category, 11 idle category, ...).
    Its `generators` are `hkbClipGenerator`s, one per weapon category that has the animation ("a046_037110",
    "a044_037110", "a052_037110", "a235_037110"). At runtime the engine picks the child whose category matches the
    weapon (spAtkcategory override first, then wepmotionCategory); a category with no child falls back to the base
    category's clip while the TAE *events* still come from the override TAE. So a new TAE category plays its events
    over the base weapon's clips until the graph gets children for it - which is what this module adds.
  * The clip file itself is resolved through the TAE record (category, animId): own name or import. The vanilla Spread
    Crossbow child "a235_033030" plays a046_033030 through such an import.
  * `animationInternalId` (hkInt16 at +198) is a per-animation-name id. The base game assigns it as the index into
    `hkbBehaviorGraphStringData.animationNames`, but the DLC categories (a083..a116) and Clever's (a678..a953) are
    not in that list at all and use ids past its end, so the list is not consulted for new clips; new ids are handed
    out past the current maximum, as FromSoft's own DLC export did.

What the edit does: for every CMSG that owns a child `a{base}_{anim}` (anim in `slots`, or all), one clone per new
category is made - name = animationName = "a{cat}_{anim}" (FromSoft's DLC style: both point at one string), a fresh
internal id, every other field copied, the `variableBindingSet` (when any) deep-copied so nothing is shared that
vanilla never shares - and appended to the CMSG's `generators` (a new array item replaces the old one, which is left
orphaned: harmless, the loader only follows pointers). Pure tagfile surgery: DATA grows at the end, ITEM/PTCH are
extended, the TYPE section is untouched.

Tagfile facts used: ITEM entry = (u32 type index | flags << 24 [0x10 object, 0x20 array], u32 DATA offset, u32 count);
pointers and hkArray data pointers in DATA are u64 item indices (an hkArray field is 16 bytes: item index, 0, 0 -
size/capacity come from ITEM); PTCH = (u32 pointer type index, u32 count, u32 sorted DATA offsets[count])* lists
every pointer location by pointer type; strings are `char` array items (NUL included in the count), shared freely.
hkbClipGenerator is 352 bytes: variableBindingSet +24, name +72, animationName +152, animationInternalId +198.
CustomManualSelectorGenerator: generators +152, offsetType +168, animId +172.
"""
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import er_hkx  # noqa: E402

CLIP_SIZE = 352
CLIP_BINDINGS, CLIP_NAME, CLIP_ANIM, CLIP_INTERNAL_ID = 24, 72, 152, 198
CMSG_GENERATORS, CMSG_OFFSET_TYPE, CMSG_ANIM_ID = 152, 168, 172
ANIM_RE = re.compile(r"^a(\d{3})_(\d{6})$")
OBJECT, ARRAY = 0x10, 0x20


class Behavior:
    """A tagfile's DATA/ITEM/PTCH taken apart; items can be appended and the file re-packed around the old TYPE."""

    def __init__(self, buf: bytes):
        self.buf = buf
        self.table = er_hkx.type_table(buf)
        self.names = [t[0] if t else None for t in self.table]
        self.data = bytearray(er_hkx.section(buf, b"DATA"))
        item = er_hkx.section(buf, b"ITEM")
        self.items = [list(struct.unpack_from("<III", item, i)) for i in range(0, len(item), 12)]  # [ref, off, count]
        ptch = er_hkx.section(buf, b"PTCH")
        self.ptch: dict[int, list[int]] = {}
        pos = 0
        while pos < len(ptch):
            ti, cnt = struct.unpack_from("<II", ptch, pos)
            pos += 8
            self.ptch[ti] = list(struct.unpack_from(f"<{cnt}I", ptch, pos))
            pos += 4 * cnt
        self.flags = {tag: fl for _, tag, _, _, fl in er_hkx.sections(buf)}
        self._ptr_type = {o: ti for ti, offs in self.ptch.items() for o in offs}   # DATA offset -> pointer type
        self._ends = sorted(e[1] for e in self.items[1:]) + [len(self.data)]

    # ---------------------------------------------------------------- queries
    def item_type(self, i: int) -> str | None:
        return self.names[self.items[i][0] & 0xFFFFFF] if self.items[i][0] & 0xFFFFFF else None

    def items_of(self, type_name: str) -> list[int]:
        return [i for i in range(len(self.items)) if self.item_type(i) == type_name]

    def item_size(self, i: int) -> int:
        import bisect
        off = self.items[i][1]
        return self._ends[bisect.bisect_right(self._ends, off)] - off

    def cstr(self, i: int) -> str:
        _, off, cnt = self.items[i]
        return bytes(self.data[off:off + cnt]).split(b"\0")[0].decode("latin1")

    def ptr(self, data_off: int) -> int:
        return struct.unpack_from("<Q", self.data, data_off)[0]

    def pointers_in(self, i: int) -> list[tuple[int, int]]:
        """(relative offset, pointer type index) of every patched pointer inside item i's DATA."""
        off, size = self.items[i][1], self.item_size(i)
        return [(o - off, self._ptr_type[o]) for o in range(off, off + size, 4) if o in self._ptr_type]

    def clip_anim(self, i: int) -> str:
        p = self.ptr(self.items[i][1] + CLIP_ANIM)
        return self.cstr(p) if p else ""

    def cmsg_children(self, c: int) -> tuple[int, list[int]]:
        arr = self.ptr(self.items[c][1] + CMSG_GENERATORS)
        _, aoff, cnt = self.items[arr]
        return arr, [self.ptr(aoff + 8 * k) for k in range(cnt)]

    def cmsg_info(self, c: int) -> tuple[int, int]:
        off = self.items[c][1]
        return struct.unpack_from("<i", self.data, off + CMSG_ANIM_ID)[0], struct.unpack_from("<i", self.data, off + CMSG_OFFSET_TYPE)[0]

    # ---------------------------------------------------------------- allocation
    def _align(self, n: int):
        while len(self.data) % n:
            self.data.append(0)

    def _patch(self, data_off: int, ptr_type: int, target: int):
        struct.pack_into("<Q", self.data, data_off, target)
        self.ptch.setdefault(ptr_type, []).append(data_off)
        self._ptr_type[data_off] = ptr_type

    def add_string(self, s: str) -> int:
        if not hasattr(self, "_char_type"):
            self._char_type = next(e[0] & 0xFFFFFF for e in self.items[1:]
                                   if (e[0] >> 24) == ARRAY and self.names[e[0] & 0xFFFFFF] == "char")
        char_type = self._char_type
        self._align(2)
        raw = s.encode("latin1") + b"\0"
        self.items.append([char_type | (ARRAY << 24), len(self.data), len(raw)])
        self.data += raw
        return len(self.items) - 1

    def add_raw(self, raw: bytes, ref: int, count: int, align: int = 16) -> int:
        self._align(align)
        self.items.append([ref, len(self.data), count])
        self.data += raw
        return len(self.items) - 1

    def deep_copy(self, i: int, overrides: dict[int, int] | None = None, share=("char",)) -> int:
        """Copy item i and, recursively, everything it points at (except shared kinds, e.g. strings).
        `overrides` maps a relative pointer offset to the item index to store there instead of copying."""
        if self.item_type(i) in share:
            return i
        ref, off, cnt = self.items[i]
        raw = bytearray(self.data[off:off + self.item_size(i)])
        pointers = self.pointers_in(i)
        new = self.add_raw(bytes(raw), ref, cnt, 16 if (ref >> 24) == OBJECT else 8)
        base = self.items[new][1]
        for rel, ptype in pointers:
            if overrides and rel in overrides:
                target = overrides[rel]
            else:
                old = struct.unpack_from("<Q", self.data, base + rel)[0]
                target = self.deep_copy(old, share=share) if old else 0
            self._patch(base + rel, ptype, target)
        return new

    def add_ptr_array(self, like: int, targets: list[int]) -> int:
        """A new pointer array item typed like array item `like`, holding `targets`."""
        ref, loff, lcnt = self.items[like]
        ptype = self._ptr_type[loff] if lcnt else self._pointer_type_of_array(ref)
        new = self.add_raw(b"\0" * (8 * len(targets)), ref, len(targets), 16)
        base = self.items[new][1]
        for k, t in enumerate(targets):
            self._patch(base + 8 * k, ptype, t)
        return new

    def _pointer_type_of_array(self, ref: int) -> int:
        name, templates = self.table[ref & 0xFFFFFF]
        return dict(templates)["tT"]

    # ---------------------------------------------------------------- output
    def build(self) -> bytes:
        self._align(16)
        item = b"".join(struct.pack("<III", *e) for e in self.items)
        ptch = b"".join(struct.pack("<II", ti, len(offs)) + struct.pack(f"<{len(offs)}I", *sorted(set(offs)))
                        for ti, offs in self.ptch.items())
        indx = er_hkx.pack_section(b"INDX", er_hkx.pack_section(b"ITEM", item, self.flags.get(b"ITEM", 1))
                                   + er_hkx.pack_section(b"PTCH", ptch, self.flags.get(b"PTCH", 1)), self.flags.get(b"INDX", 0))
        sdkv = er_hkx._section_raw(self.buf, b"SDKV")
        type_sec = er_hkx._section_raw(self.buf, b"TYPE")
        data_sec = er_hkx.pack_section(b"DATA", bytes(self.data), self.flags.get(b"DATA", 1))
        return er_hkx.pack_section(b"TAG0", sdkv + data_sec + type_sec + indx, self.flags.get(b"TAG0", 0))


STRING_DATA_ANIMATION_NAMES = 88     # hkbBehaviorGraphStringData.animationNames (hkArray<hkStringPtr>)


def animation_names(b: Behavior) -> tuple[int, int, list[str]]:
    """(string-data item, array item, names) of hkbBehaviorGraphStringData.animationNames."""
    sd = b.items_of("hkbBehaviorGraphStringData")[0]
    arr = b.ptr(b.items[sd][1] + STRING_DATA_ANIMATION_NAMES)
    _, aoff, cnt = b.items[arr]
    return sd, arr, [b.cstr(b.ptr(aoff + 8 * k)) for k in range(cnt)]


def register_categories(beh: bytes, base_cat: int, new_cats: list[int], slots=None, verbose=True,
                        list_names: bool = False) -> tuple[bytes, str]:
    """Give every CMSG that owns an a{base_cat} clip (for `slots`, or all) a child clip per category in `new_cats`.

    `list_names=True` additionally appends the new animations' full paths to the string data's `animationNames`
    and uses their list indices as internal ids (HkbEditor's convention for base-game clips). Off by default: the
    DLC and Clever's categories are not listed and work, and the list's indices already collide with DLC ids."""
    b = Behavior(beh)
    clips = b.items_of("hkbClipGenerator")
    cmsgs = b.items_of("CustomManualSelectorGenerator")
    existing: dict[str, int] = {}
    max_id = -1
    for i in clips:
        existing.setdefault(b.clip_anim(i), i)
        max_id = max(max_id, struct.unpack_from("<h", b.data, b.items[i][1] + CLIP_INTERNAL_ID)[0])
    next_id = max_id + 1
    sd_item, names_arr, names = animation_names(b)
    if list_names:
        next_id = len(names)
    # which base clips are owned by which CMSGs
    owners: dict[int, list[int]] = {}
    for c in cmsgs:
        _, children = b.cmsg_children(c)
        for k in children:
            if b.item_type(k) == "hkbClipGenerator":
                m = ANIM_RE.match(b.clip_anim(k))
                if m and int(m.group(1)) == base_cat and (slots is None or int(m.group(2)) in slots):
                    owners.setdefault(k, []).append(c)
    if not owners:
        raise ValueError(f"no selector owns an a{base_cat:03d} clip for the requested slots")
    if next_id + len(new_cats) * len({b.clip_anim(k) for k in owners}) > 32767:
        raise ValueError("animationInternalId (int16) would overflow")
    # one clone per (base clip, category); a clone is shared by every CMSG that shares the base clip, as vanilla does
    clones: dict[tuple[int, int], int] = {}
    anim_ids: dict[str, int] = {}
    added = 0
    for base in owners:
        anim = int(ANIM_RE.match(b.clip_anim(base)).group(2))
        for cat in new_cats:
            key = f"a{cat:03d}_{anim:06d}"
            if key in existing and (base, cat) not in clones:
                clones[(base, cat)] = existing[key]       # the graph already has this animation: reuse it
                continue
            name = b.add_string(key)
            clone = b.deep_copy(base, overrides={CLIP_NAME: name, CLIP_ANIM: name})
            if key not in anim_ids:
                anim_ids[key] = next_id
                next_id += 1
            struct.pack_into("<h", b.data, b.items[clone][1] + CLIP_INTERNAL_ID, anim_ids[key])
            clones[(base, cat)] = clone
            added += 1
    # extend the owning selectors
    extended = 0
    for c in cmsgs:
        arr, children = b.cmsg_children(c)
        present = {b.clip_anim(k)[1:4] for k in children if b.item_type(k) == "hkbClipGenerator"}
        add = []
        for k in children:
            if k in owners:
                for cat in new_cats:
                    if f"{cat:03d}" not in present:
                        add.append(clones[(k, cat)])
                        present.add(f"{cat:03d}")
        if add:
            new_arr = b.add_ptr_array(arr, children + add)
            struct.pack_into("<Q", b.data, b.items[c][1] + CMSG_GENERATORS, new_arr)
            extended += 1
    listed = ""
    if list_names and anim_ids:
        # full paths in the vanilla style, appended in internal-id order so that id == list index
        template = names[-1].rsplit("\\", 2)[0]
        new_names = [f"{template}\\{key[:4]}\\{key}.hkx" for key, _ in sorted(anim_ids.items(), key=lambda kv: kv[1])]
        _, aoff, _ = b.items[names_arr]
        targets = [b.ptr(aoff + 8 * k) for k in range(len(names))] + [b.add_string(n) for n in new_names]
        new_list = b.add_ptr_array(names_arr, targets)
        struct.pack_into("<Q", b.data, b.items[sd_item][1] + STRING_DATA_ANIMATION_NAMES, new_list)
        listed = f"; {len(new_names)} names listed in animationNames ({len(names)} -> {len(targets)})"
    out = b.build()
    report = (f"{added} clip generators added ({len(anim_ids)} animations x categories {new_cats[0]}..{new_cats[-1]}) "
              f"over {len(owners)} a{base_cat:03d} clips in {extended} selectors; internal ids "
              f"{min(anim_ids.values()) if anim_ids else '-'}..{next_id - 1}{listed}; {len(beh) / 1e6:.1f} -> {len(out) / 1e6:.1f} MB")
    if verbose:
        print(report)
    return out, report


def validate(beh: bytes) -> dict:
    """Structural checks: every patched pointer holds a live item index, items lie inside DATA, PTCH offsets in range."""
    b = Behavior(beh)
    n = len(b.items)
    bad_ptr = sum(1 for offs in b.ptch.values() for o in offs if o + 8 > len(b.data) or b.ptr(o) >= n)
    bad_item = sum(1 for ref, off, cnt in b.items[1:] if off > len(b.data))
    unsorted = sum(1 for offs in b.ptch.values() if offs != sorted(offs))
    return {"items": n, "data": len(b.data), "bad_pointers": bad_ptr, "bad_items": bad_item, "unsorted_ptch_groups": unsorted,
            "clips": len(b.items_of("hkbClipGenerator")), "selectors": len(b.items_of("CustomManualSelectorGenerator"))}


def children_report(beh: bytes, anim_name: str) -> list[str]:
    b = Behavior(beh)
    target = {i for i in b.items_of("hkbClipGenerator") if b.clip_anim(i) == anim_name}
    out = []
    for c in b.items_of("CustomManualSelectorGenerator"):
        _, ch = b.cmsg_children(c)
        if target.intersection(ch):
            aid, ot = b.cmsg_info(c)
            out.append(f"selector {c} animId {aid} offsetType {ot}: {[b.clip_anim(k)[:4] for k in ch]}")
    return out


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        raise SystemExit("usage: er_behavior.py <Behaviors c0000.hkx> <animation name, e.g. a046_037110> [...]")
    buf = Path(args[0]).read_bytes()
    print(validate(buf))
    for name in args[1:]:
        for line in children_report(buf, name) or [f"{name}: owned by no selector"]:
            print(line)


if __name__ == "__main__":
    main()
