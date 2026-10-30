"""Convert Dark Souls (PTDE, Havok 2010 packfile) animation HKX files to Elden Ring (Havok 2018 tagfile).

The spline-compressed track data is carried over byte-for-byte; only the Havok container/type layout changes.
Bone bindings are NOT retargeted here (tracks still refer to the source skeleton's bone indices) — this is the
format step; retargeting is a separate tool.

Usage:
    convert_anim_ds1_to_er.py OUT_DIR IN.hkx [IN2.hkx ...]
    convert_anim_ds1_to_er.py --verify OUT_DIR IN.hkx ...   # also re-read output and compare every pose

Files exported with Havok Content Tools (extra 'Resource Data' variant) are handled.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import modkit_env  # noqa: F401,E402
from ds1_anim_load import load_ds1_animation  # noqa: E402
from soulstruct.dcx import DCXType  # noqa: E402
from soulstruct.havok.fromsoft.eldenring import AnimationHKX as AnimationHKX_ER  # noqa: E402


def quat(q):
    return np.array([q.x, q.y, q.z, q.w])


def poses(animation_hkx):
    ac = animation_hkx.animation_container
    ac.load_spline_data()
    inter = ac.to_interleaved_container()
    inter.load_interleaved_data()
    return inter.interleaved_data


def convert(src: Path, out_dir: Path, verify: bool = False) -> Path:
    anim = load_ds1_animation(src)
    er = AnimationHKX_ER.from_2010_hkx(anim, dcx_type=DCXType.Null)
    dst = out_dir / src.name
    er.write(dst)
    if verify:
        back = AnimationHKX_ER.from_path(dst)
        a, b = poses(anim), poses(back)
        maxd = 0.0
        for fa, fb in zip(a, b):
            for ta, tb in zip(fa, fb):
                maxd = max(maxd, float(np.max(np.abs(np.asarray(ta.translation) - np.asarray(tb.translation)))),
                           float(np.max(np.abs(quat(ta.rotation) - quat(tb.rotation)))))
        bc = back.animation_container
        print(f"  {src.name}: {bc.frame_count} frames x {bc.track_count} tracks -> {dst.stat().st_size} B, max pose diff {maxd:.1e}")
    else:
        print(f"  {src.name} -> {dst}")
    return dst


def main():
    args = sys.argv[1:]
    verify = False
    if args and args[0] == "--verify":
        verify = True
        args = args[1:]
    out_dir = Path(args[0])
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    for p in args[1:]:
        try:
            convert(Path(p), out_dir, verify)
            ok += 1
        except Exception as e:  # keep going; report at the end
            fail += 1
            print(f"  FAILED {p}: {type(e).__name__}: {e}")
    print(f"converted {ok}, failed {fail}")


if __name__ == "__main__":
    main()
