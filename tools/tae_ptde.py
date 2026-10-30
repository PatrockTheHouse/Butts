"""Parser for Dark Souls PTDE (32-bit, version 0x1000B) TAE files.

Prints animations, their HKX references and every event with start/end times and raw data ints.
"""
import struct
import sys
from pathlib import Path


def u16str(buf: bytes, off: int) -> str:
    end = off
    while buf[end:end + 2] != b"\x00\x00":
        end += 2
    return buf[off:end].decode("utf-16-le", errors="replace")


def parse_tae_ptde(buf: bytes):
    assert buf[:4] == b"TAE ", buf[:4]
    version = struct.unpack_from("<I", buf, 8)[0]
    assert version == 0x1000B, hex(version)
    tae_id, anim_count, anims_off, groups_off = struct.unpack_from("<iiII", buf, 0x50)
    skel_off, sib_off = struct.unpack_from("<II", buf, 0x98)
    skeleton = u16str(buf, skel_off)
    sib = u16str(buf, sib_off)
    anims = []
    for i in range(anim_count):
        anim_id, aoff = struct.unpack_from("<iI", buf, anims_off + i * 8)
        (ev_count, ev_hdr_off, ev_grp_count, ev_grp_off,
         ev_times_count, ev_times_off, anim_file_off) = struct.unpack_from("<iIiIiII", buf, aoff)
        # animation file struct: is_reference(?), then offsets
        is_ref = buf[anim_file_off]
        name_off_ptr = struct.unpack_from("<I", buf, anim_file_off + 4)[0]
        name_off = struct.unpack_from("<I", buf, anim_file_off + 8)[0]
        unk18, unk1c = struct.unpack_from("<ii", buf, anim_file_off + 12)
        anim_name = ""
        if 0 < name_off < len(buf):
            try:
                anim_name = u16str(buf, name_off)
            except Exception:
                anim_name = ""
            if not anim_name.lower().endswith((".hkx", ".hkt")):
                anim_name = ""
        events = []
        for j in range(ev_count):
            st_off, et_off, data_off = struct.unpack_from("<III", buf, ev_hdr_off + j * 12)
            st = struct.unpack_from("<f", buf, st_off)[0]
            et = struct.unpack_from("<f", buf, et_off)[0]
            ev_type = struct.unpack_from("<i", buf, data_off)[0]
            payload_off = struct.unpack_from("<I", buf, data_off + 4)[0]
            events.append(dict(type=ev_type, start=st, end=et, data_off=data_off, payload_off=payload_off))
        # payload sizes: gap to next payload (events' data blocks are contiguous)
        offs = sorted({e["data_off"] for e in events})
        for e in events:
            nxt = [o for o in offs if o > e["payload_off"]]
            end = nxt[0] if nxt else e["payload_off"] + 16
            e["payload"] = buf[e["payload_off"]:end]
        anims.append(dict(id=anim_id, is_ref=is_ref, unk18=unk18, unk1c=unk1c, name=anim_name, events=events))
    return dict(tae_id=tae_id, skeleton=skeleton, sib=sib, animations=anims)


def fmt_payload(p: bytes) -> str:
    ints = struct.unpack_from("<" + "i" * (len(p) // 4), p, 0)
    floats = struct.unpack_from("<" + "f" * (len(p) // 4), p, 0)
    parts = []
    for i, f in zip(ints, floats):
        if -100000 < i < 100000000:
            parts.append(str(i))
        elif 1e-3 < abs(f) < 1e5:
            parts.append(f"{f:.3f}f")
        else:
            parts.append(hex(i & 0xFFFFFFFF))
    return "[" + ", ".join(parts) + "]"


def main():
    for path in sys.argv[1:]:
        tae = parse_tae_ptde(Path(path).read_bytes())
        print(f"##### {Path(path).name}: tae_id={tae['tae_id']} skeleton={tae['skeleton']} sib={tae['sib']} anims={len(tae['animations'])}")
        for a in tae["animations"]:
            ref = f"ref(unk18={a['unk18']}, unk1c={a['unk1c']})" if a["is_ref"] else ""
            print(f"  anim {a['id']:>4}  file='{a['name']}' {ref}  events={len(a['events'])}")
            for e in sorted(a["events"], key=lambda e: (e["start"], e["type"])):
                print(f"      t={e['start']:6.3f}-{e['end']:6.3f}  type={e['type']:<4} {fmt_payload(e['payload'])}")


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    main()
