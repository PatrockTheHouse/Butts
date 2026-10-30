"""Parser for 64-bit TAE (DSR 0x1000C / DS3-ER 0x1000D). Mirrors tae_ptde.py's output structure."""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tae_ptde import fmt_payload, u16str  # noqa: E402


def parse_tae64(buf: bytes):
    assert buf[:4] == b"TAE ", buf[:4]
    version = struct.unpack_from("<I", buf, 8)[0]
    assert version in (0x1000C, 0x1000D), hex(version)
    tae_id, anim_count = struct.unpack_from("<ii", buf, 0x50)
    anims_off, groups_off = struct.unpack_from("<QQ", buf, 0x58)
    skel_off, sib_off = struct.unpack_from("<QQ", buf, 0xB0)
    skeleton = u16str(buf, skel_off) if skel_off else ""
    sib = u16str(buf, sib_off) if sib_off else ""
    anims = []
    for i in range(anim_count):
        anim_id, aoff = struct.unpack_from("<qQ", buf, anims_off + i * 16)
        (ev_hdr_off, ev_grp_off, ev_times_off, anim_file_off,
         ev_count, ev_grp_count, ev_times_count, _z) = struct.unpack_from("<QQQQiiii", buf, aoff)
        is_ref = buf[anim_file_off]
        name_off = struct.unpack_from("<Q", buf, anim_file_off + 16)[0]
        unk18, unk1c = struct.unpack_from("<ii", buf, anim_file_off + 24)
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
            st_off, et_off, data_off = struct.unpack_from("<QQQ", buf, ev_hdr_off + j * 24)
            st = struct.unpack_from("<f", buf, st_off)[0]
            et = struct.unpack_from("<f", buf, et_off)[0]
            ev_type = struct.unpack_from("<q", buf, data_off)[0]
            payload_off = struct.unpack_from("<Q", buf, data_off + 8)[0]
            events.append(dict(type=ev_type, start=st, end=et, data_off=data_off, payload_off=payload_off))
        offs = sorted({e["data_off"] for e in events})
        for e in events:
            nxt = [o for o in offs if o > e["payload_off"]]
            end = nxt[0] if nxt else e["payload_off"] + 16
            e["payload"] = buf[e["payload_off"]:end]
        anims.append(dict(id=anim_id, is_ref=is_ref, unk18=unk18, unk1c=unk1c, name=anim_name, events=events))
    return dict(tae_id=tae_id, skeleton=skeleton, sib=sib, animations=anims, version=version)


def main():
    verbose = "-v" in sys.argv
    for path in [p for p in sys.argv[1:] if p != "-v"]:
        tae = parse_tae64(Path(path).read_bytes())
        print(f"##### {Path(path).name}: v{tae['version']:#x} tae_id={tae['tae_id']} anims={len(tae['animations'])}")
        for a in tae["animations"]:
            ref = f"ref(unk18={a['unk18']}, unk1c={a['unk1c']})" if a["is_ref"] else ""
            types = {}
            for e in a["events"]:
                types[e["type"]] = types.get(e["type"], 0) + 1
            print(f"  anim {a['id']:>6}  file='{a['name']}' {ref}  events={len(a['events'])}  types={dict(sorted(types.items()))}")
            if verbose:
                for e in sorted(a["events"], key=lambda e: (e["start"], e["type"])):
                    print(f"      t={e['start']:6.3f}-{e['end']:6.3f}  type={e['type']:<4} {fmt_payload(e['payload'])}")


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    main()
