"""Load DS1 PTDE animation HKX files, including Havok-Content-Tools exports that carry a 'Resource Data' stub variant."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import modkit_env  # noqa: F401,E402

from soulstruct.havok.core import HKX  # noqa: E402
from soulstruct.havok.fromsoft.darksouls1ptde import AnimationHKX as AnimationHKX_PTDE  # noqa: E402


def load_ds1_animation(path: Path) -> AnimationHKX_PTDE:
    """Read a DS1 animation packfile as a generic HKX, drop any `hkMemoryResourceContainer` variant, and wrap it."""
    generic = HKX.from_path(path)
    root = generic.root
    kept = [v for v in root.namedVariants if v.className != "hkMemoryResourceContainer"]
    if not kept:
        raise ValueError(f"{path.name}: no animation container variant found")
    root.namedVariants = kept
    return AnimationHKX_PTDE(
        root=root,
        hk_format=generic.hk_format,
        hk_version=generic.hk_version,
        unpacker=generic.unpacker,
        is_big_endian=generic.is_big_endian,
        hsh_overrides=generic.hsh_overrides,
        packfile_header_info=generic.packfile_header_info,
    )


if __name__ == "__main__":
    for p in sys.argv[1:]:
        a = load_ds1_animation(Path(p))
        ac = a.animation_container
        print(f"{Path(p).name}: frames={ac.frame_count} tracks={ac.track_count} spline={ac.is_spline} "
              f"duration={ac.hkx_animation.duration:.3f}s")
