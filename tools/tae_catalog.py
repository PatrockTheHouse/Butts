"""Summarize gun TAE categories: per animation, the bullet judge IDs, FFX IDs, sounds, SpEffects and jump tables."""
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tae_ptde import parse_tae_ptde  # noqa: E402


def ints(p: bytes, n: int):
    return list(struct.unpack_from("<" + "i" * n, p, 0)) if len(p) >= 4 * n else list(struct.unpack_from("<" + "i" * (len(p) // 4), p, 0))


def main():
    total_anims = 0
    total_events = 0
    type_counter = Counter()
    for path in sys.argv[1:]:
        tae = parse_tae_ptde(Path(path).read_bytes())
        print(f"##### {Path(path).name} (tae_id {tae['tae_id']}): {len(tae['animations'])} animations")
        for a in tae["animations"]:
            total_anims += 1
            total_events += len(a["events"])
            bullets, ffx, sounds, speff, jumps, others = [], [], [], [], set(), Counter()
            for e in a["events"]:
                type_counter[e["type"]] += 1
                v = ints(e["payload"], 4)
                t = e["type"]
                if t == 2:
                    bullets.append(v[2])
                elif t == 96:
                    ffx.append(v[0])
                elif t in (128, 129, 130, 131, 132):
                    sounds.append((v[0], v[1]))
                elif t in (66, 67, 401):
                    speff.append(v[0])
                elif t == 0:
                    jumps.add(v[0])
                else:
                    others[t] += 1
            dur = max((e["end"] for e in a["events"]), default=0)
            ref = f" -> ref {a['unk18']}" if a["is_ref"] else ""
            line = f"  {a['id']:>5}{ref:<16} len~{dur:4.2f}s ev={len(a['events']):>2}"
            if bullets:
                c = Counter(bullets)
                line += f"  bullets={dict(sorted(c.items()))}"
            if ffx:
                line += f"  ffx={sorted(set(ffx))}"
            if sounds:
                line += f"  snd={sorted(set(sounds))}"
            if speff:
                line += f"  speff={sorted(set(speff))}"
            if others:
                line += f"  other={dict(sorted(others.items()))}"
            print(line)
    print(f"\nTOTAL: {total_anims} animations, {total_events} events; event types: {dict(sorted(type_counter.items()))}")


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    main()
