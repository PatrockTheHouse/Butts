#!/usr/bin/env bash
# Fetches the third-party libraries the toolkit depends on, at pinned commits, into vendor/,
# applies our local patches, and writes small import shims for optional dependencies that are
# not needed for file conversion (rich, typer, zstandard, frozendict).
#
# Usage:  bash vendor/setup.sh            (idempotent; re-run to repair)
#         bash vendor/setup.sh --update   (re-clone at the pinned commits even if present)
#
# Afterwards, scripts in tools/ import `modkit_env` which puts these on sys.path.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# repo                                   dir                 pinned commit
PINS=(
  "https://github.com/Grimrukh/soulstruct        soulstruct          12b69189a2ccebbc623a1b6565be89a18d6c9958"
  "https://github.com/Grimrukh/soulstruct-havok  soulstruct-havok    bf2d41fc83de4a43bd3ed3df8605741f143cad2c"
  "https://github.com/Grimrukh/constrata         constrata           b46a942c3cf030c2e330e61e4fb61d2cba82daae"
  "https://github.com/soulsmods/Paramdex         paramdex            ff7245e524329bc3eab00036723d2bd53384cedf"
  "https://github.com/Meowmaritus/DSAnimStudio   dsanimstudio        f1bff06cd422de991b0a0fa8a2da81db43417318"
  "https://github.com/powzix/ooz                 ooz                 05038060aa68f9187ae9923b2388ca8db40e58d1"
)

UPDATE=0
[[ "${1:-}" == "--update" ]] && UPDATE=1

fetch() {
  local url="$1" dir="$2" commit="$3"
  if [[ -d "$dir/.git" && $UPDATE -eq 0 ]]; then
    echo "[vendor] $dir present ($(git -C "$dir" rev-parse --short HEAD))"
    return
  fi
  rm -rf "$dir"
  echo "[vendor] cloning $url @ ${commit:0:12}"
  # Shallow clone of the default branch, then try to reach the pinned commit.
  GIT_LFS_SKIP_SMUDGE=1 git clone -q --depth 1 "$url" "$dir"
  if [[ "$(git -C "$dir" rev-parse HEAD)" != "$commit" ]]; then
    git -C "$dir" fetch -q --depth 200 origin "$commit" 2>/dev/null || true
    git -C "$dir" checkout -q "$commit" 2>/dev/null \
      || echo "[vendor] WARNING: $dir is at $(git -C "$dir" rev-parse --short HEAD), not the pinned ${commit:0:12}"
  fi
}

for pin in "${PINS[@]}"; do
  # shellcheck disable=SC2086
  fetch $pin
done

# --- patches -------------------------------------------------------------------------------
# soulstruct-havok: two tagfile-writer fixes needed for Elden Ring (hk2018) output, plus the
# Havok 2010 resource-container types that Havok Content Tools exports carry.
if git -C soulstruct-havok apply --check --reverse "$HERE/patches/soulstruct-havok.patch" 2>/dev/null; then
  echo "[vendor] soulstruct-havok patch already applied"
else
  git -C soulstruct-havok apply "$HERE/patches/soulstruct-havok.patch"
  echo "[vendor] applied soulstruct-havok patch"
fi

# soulstruct: read/write the Sekiro/Elden Ring FLVER unknown struct (`SekiroUnkStruct`) that upstream skips, so
# FLVERs written for Elden Ring (version 0x2001A) carry it like the game's own files do.
if git -C soulstruct apply --check --reverse "$HERE/patches/soulstruct.patch" 2>/dev/null; then
  echo "[vendor] soulstruct patch already applied"
else
  git -C soulstruct apply "$HERE/patches/soulstruct.patch"
  echo "[vendor] applied soulstruct patch"
fi

# ooz (open-source Oodle Kraken/Mermaid/Leviathan decompressor, MSVC-only upstream): Linux stdafx.h shim,
# a seek-chunk-reset fix needed for FromSoftware's DCX KRAK streams, and the small `oozdec` CLI that
# tools/dcx.py drives (oozdec IN OUT UNCOMPRESSED_SIZE). Decompression only; we write DCX as DFLT.
if git -C ooz apply --check --reverse "$HERE/patches/ooz-linux.patch" 2>/dev/null; then
  echo "[vendor] ooz patch already applied"
else
  git -C ooz apply "$HERE/patches/ooz-linux.patch"
  echo "[vendor] applied ooz patch"
fi
if [[ ! -x ooz/oozdec || $UPDATE -eq 1 || ooz/oozdec -ot "$HERE/patches/ooz-linux.patch" ]]; then
  if command -v g++ >/dev/null; then
    echo "[vendor] building ooz/oozdec"
    OOZFLAGS="-O2 -std=c++17 -fpermissive -w -msse4.1"
    (cd ooz && g++ $OOZFLAGS -Dmain=ooz_cli_main -c kraken.cpp -o kraken.o \
      && g++ $OOZFLAGS -c lzna.cpp -o lzna.o && g++ $OOZFLAGS -c bitknit.cpp -o bitknit.o \
      && g++ $OOZFLAGS -o oozdec oozdec.cpp kraken.o lzna.o bitknit.o && rm -f kraken.o lzna.o bitknit.o)
  else
    echo "[vendor] WARNING: g++ not found; DCX KRAK files cannot be read until vendor/ooz/oozdec is built"
  fi
fi

# --- shims ---------------------------------------------------------------------------------
# Soulstruct imports these at module load but the toolkit never exercises them:
#   rich      -> console/traceback decoration only
#   typer     -> CLI entry point only
#   zstandard -> Elden Ring regulation DCX (we decompress ZSTD through Node instead)
#   frozendict-> trivial immutable dict
mkdir -p shims/rich shims/typer shims/zstandard shims/frozendict
cat > shims/rich/__init__.py <<'EOF'
"""Minimal stand-in for `rich` (console output only)."""
EOF
cat > shims/rich/console.py <<'EOF'
class Console:
    def __init__(self, *a, **k): pass
    def print(self, *a, **k): print(*[str(x) for x in a])
    def log(self, *a, **k): print(*[str(x) for x in a])
    def rule(self, *a, **k): print("-" * 60)
    def print_exception(self, *a, **k):
        import traceback; traceback.print_exc()
EOF
cat > shims/rich/traceback.py <<'EOF'
def install(*a, **k): return None
EOF
cat > shims/rich/logging.py <<'EOF'
import logging
class RichHandler(logging.StreamHandler):
    def __init__(self, *a, **k): super().__init__()
EOF
cat > shims/typer/__init__.py <<'EOF'
class Typer:
    def __init__(self, *a, **k): pass
    def command(self, *a, **k):
        def deco(f): return f
        return deco
    def callback(self, *a, **k):
        def deco(f): return f
        return deco
    def __call__(self, *a, **k): raise SystemExit("typer shim: CLI unavailable")
def Option(*a, **k): return None
def Argument(*a, **k): return None
def echo(*a, **k): print(*a)
EOF
cat > shims/zstandard/__init__.py <<'EOF'
class ZstdError(Exception): pass
class ZstdCompressor:
    def __init__(self, *a, **k): pass
    def compress(self, data): raise NotImplementedError("zstandard shim: use tools/er_regulation.py (Node zstd) instead")
class ZstdDecompressor:
    def __init__(self, *a, **k): pass
    def decompress(self, data, *a, **k): raise NotImplementedError("zstandard shim: use tools/er_regulation.py (Node zstd) instead")
EOF
cat > shims/frozendict/__init__.py <<'EOF'
class frozendict(dict):
    """Minimal immutable dict."""
    def __hash__(self): return hash(frozenset(self.items()))
    def _ro(self, *a, **k): raise TypeError("frozendict is immutable")
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _ro
    def __or__(self, other): return frozendict({**self, **other})
    def set(self, k, v): return frozendict({**self, k: v})
    def delete(self, k):
        d = dict(self); del d[k]; return frozendict(d)
EOF

echo "[vendor] done. Python needs: python>=3.13, numpy, scipy, cryptography; Node>=22.15 for ZSTD; g++ for KRAK."
