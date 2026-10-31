# fromsoft-modkit

Command-line tools and reference notes for working with FromSoftware game files — built while porting a
Dark Souls: Prepare to Die Edition weapon mod to Elden Ring. Linux-friendly (no Windows-only tools required);
Python 3.13+, Node 22.15+ for Elden Ring's ZSTD-compressed regulation, g++ for Oodle (KRAK) decompression, cmake for the BC7 encoder.

Start with **[docs/reference.md](docs/reference.md)** — file layouts, PARAM/TAE format details, the Elden Ring
crossbow slot map, how Ashes of War are wired, and the animation conversion pipeline.

## Setup

```bash
bash vendor/setup.sh          # clones pinned Soulstruct, Soulstruct-Havok, constrata, Paramdex, DSAnimStudio templates, ooz, bc7enc_rdo; applies patches, builds oozdec + bc7enc (g++, cmake)
pip install numpy scipy cryptography
```

Nothing is copied from those projects into this repo; `vendor/` is populated locally (and git-ignored) at pinned
commits, with our fixes applied from `vendor/patches/`.

## Tools (`tools/`)

| Tool | Purpose |
|---|---|
| `bnd_tools.py FILE` | List any BND3/BND4 binder, DCX-compressed or not |
| `dcx.py IN [OUT]` / `--pack IN OUT` | Unwrap any DCX (DFLT, KRAK via vendored ooz, ZSTD via Node) or wrap as DFLT; `compress_zstd` for ER 1.12+ regulation |
| `bnd3_list.py BND` / `dump_rows.py BND Param [regex]` | DS1 GameParam: list PARAMs, dump row IDs/names |
| `paramdecode.py BND Param ids…` / `--diff base others…` | DS1 rows decoded with the real paramdefs; diff rows field by field |
| `tae_ptde.py` / `tae64.py [-v] TAE…` | Dump TAE animations and events (DS1 32-bit / DSR-DS3-ER 64-bit) |
| `tae3.py roundtrip|dump TAE…` | Elden Ring TAE reader **and writer** (byte-identical round trip); import as a library to build TAEs |
| `tae_events.py` / `tae_dump.py TAE [ids] [--types …]` | Encode/decode ER event payloads by field name; readable event dump with group types |
| `bnd4.py BINDER` | BND4 reader/writer (+ DCX; DFLT standalone, KRAK/ZSTD through dcx.py) — build or patch anibnd/regulation binders |
| `tae_catalog.py TAE…` | One line per animation: bullets, FFX, sounds, SpEffects, flags |
| `tae_template.py GAME [event ids]` | DSAnimStudio event definitions (field layouts, enums) for DS1/DS3/ER/… |
| `er_regulation.py regulation.bin OUT.parambnd` | Decrypt + decompress Elden Ring's regulation into a raw BND4; `write_regulation(bnd_bytes)` packs it back (ZSTD + AES) |
| `er_param.py BND Param ids…` | Decode Elden Ring PARAM rows (Paramdex defs + row names); `encode_row` / `rebuild_param` add or replace rows (64-bit PARAM writer) |
| `ds1_anim_load.py HKX…` | Load DS1 animations, including Havok Content Tools exports |
| `convert_anim_ds1_to_er.py [--verify] OUT_DIR HKX…` | Convert DS1 (hk2010) animations to Elden Ring (hk2018) tagfiles |
| `flver_info.py FILE [--verts] [--json OUT]` | Dump a FLVER (or every FLVER/TPF in a partsbnd/chrbnd): header, bones, dummies, materials, GX items, meshes, layouts, textures |
| `er_model.py` (library) | Build Elden Ring parts models: FLVER 0x2001A + TPF + partsbnd from mesh arrays, conventions learned from a vanilla reference (`ERProfile`) |
| `dds_tools.py info\|png FILE.dds` | DDS helpers: decode to RGBA, encode BC1/BC3/BC4/BC5/BC7 with mip chains (vendored bc7enc_rdo; Pillow fallback), legacy ↔ DX10 (sRGB) headers — no texconv needed |

Run tools with `python3 -I tools/<tool>.py …` (isolated mode) when the inputs are untrusted files.

## Layout

```
tools/      the scripts (import modkit_env first; it finds vendor/)
vendor/     setup.sh, patches/, shims/  (+ the cloned libraries, ignored by git)
docs/       reference notes
```

## License

Scripts and docs in this repository are the author's; choose and add a LICENSE file. Vendored projects keep
their own licenses (Soulstruct and Soulstruct-Havok are GPL-3.0; bc7enc_rdo MIT; ooz is "open source Kraken decompressor" by powzix, no license file upstream; Paramdex and DSAnimStudio see upstream).
