"""Locates the vendored libraries (see vendor/setup.sh) and puts them on sys.path.

Import this first in any tool that needs Soulstruct / Soulstruct-Havok / Paramdex:

    import modkit_env  # noqa: F401
    from soulstruct.havok.fromsoft.eldenring import AnimationHKX

Paths can be overridden with the MODKIT_VENDOR environment variable.
"""
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR = Path(os.environ.get("MODKIT_VENDOR", REPO_ROOT / "vendor"))

SOULSTRUCT_SRC = VENDOR / "soulstruct" / "src"
SOULSTRUCT_HAVOK_SRC = VENDOR / "soulstruct-havok" / "src"
CONSTRATA = VENDOR / "constrata"
SHIMS = VENDOR / "shims"
PARAMDEX = VENDOR / "paramdex"
DSAS_RES = VENDOR / "dsanimstudio" / "DSAnimStudioNETCore" / "Res"

DS1_PARAMDEF_BND = SOULSTRUCT_SRC / "soulstruct" / "darksouls1ptde" / "params" / "resources" / "paramdef.paramdefbnd"

for p in (SOULSTRUCT_SRC, SOULSTRUCT_HAVOK_SRC, CONSTRATA, SHIMS):
    if not p.exists():
        raise ImportError(f"vendored dependency missing: {p}  (run `bash vendor/setup.sh`)")
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

# Soulstruct logs an Oodle-DLL warning on non-Windows; silence it.
logging.disable(logging.WARNING)


def tae_template(game: str) -> Path:
    """Path to DSAnimStudio's TAE template XML for a game code: DS1, DS3, ER, SDT, BB, ..."""
    return DSAS_RES / f"TAE.Template.{game}.xml"
