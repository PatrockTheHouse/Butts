"""Elden Ring menu item icons: add a sheet of new icons to `menu/hi/01_common.sblytbnd.dcx` + `01_common.tpf.dcx`.

    import er_icons
    icons = {30000: rgba_160x160, 30001: rgba, ...}                        # iconId -> HxWx4 uint8 (any size: fit_icon())
    sheet, entries = er_icons.pack_icons(icons)                             # one RGBA sheet + SubTexture entries
    new_lyt, new_tpf = er_icons.add_sheet(sblytbnd_bytes, tpf_bytes, "SB_Icon_Halo", sheet, entries)
    # write both back as DCX (DFLT is fine) under menu/hi/

How the game finds an icon: EquipParam*.iconId (u16) -> the SubTexture named `MENU_ItemIcon_{iconId:05d}.png` in one
of the `.layout` XML files of `01_common.sblytbnd.dcx`; each layout describes one sheet (`imagePath`), a DDS texture
of the same stem inside `01_common.tpf.dcx`. Vanilla item icons are 160x160 cells on 2048x2048 sheets
(`SB_Icon_03_A.layout`: `<SubTexture name="MENU_ItemIcon_11215.png" x="984" y="1640" width="160" height="160"
half="0"/>`); the DLC added its own sheets (`SB_Icon_08_dlc`) to the same two binders, which is what this module does
for a mod: a new sheet + a new layout, nothing vanilla touched.

The exact layout header (root element and attributes) is copied from an existing layout in the binder, so the
generated file matches whatever the game's version writes; `parse_layout` reads them back.
"""
import re
import struct
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import modkit_env  # noqa: E402,F401
import numpy as np  # noqa: E402
from bnd4 import BND4, BinderFile  # noqa: E402
from soulstruct.containers.tpf import TPF, TPFTexture  # noqa: E402
import dds_tools  # noqa: E402

ICON = 160
SUBTEXTURE_NAME = "MENU_ItemIcon_{icon_id:05d}.png"


# ---------------------------------------------------------------------------------------------------- layouts
def parse_layout(xml: bytes) -> dict:
    root = ET.fromstring(xml.decode("utf-8-sig") if isinstance(xml, bytes) else xml)
    entries = []
    for st in root:
        a = st.attrib
        entries.append({"name": a.get("name"), "x": int(a.get("x", 0)), "y": int(a.get("y", 0)),
                        "width": int(a.get("width", 0)), "height": int(a.get("height", 0)), "half": a.get("half")})
    return {"tag": root.tag, "attrib": dict(root.attrib), "child_tag": root[0].tag if len(root) else "SubTexture",
            "image_path": root.attrib.get("imagePath"), "entries": entries}


def make_layout(image_stem: str, size: tuple[int, int], entries: list[dict], template: dict | None = None) -> bytes:
    """XML bytes for one sheet. `entries`: dicts with name/x/y/width/height (and half). The root element and the
    extension of imagePath follow `template` (a parsed vanilla layout) when given."""
    tag = template["tag"] if template else "TextureAtlas"
    child_tag = template["child_tag"] if template else "SubTexture"
    attrib = dict(template["attrib"]) if template else {}
    ext = Path(template["image_path"]).suffix if template and template.get("image_path") else ".png"
    attrib["imagePath"] = image_stem + ext
    if template is None or "width" in attrib:
        attrib["width"] = str(size[0])
    if template is None or "height" in attrib:
        attrib["height"] = str(size[1])
    half_default = "0"
    if template and template["entries"] and template["entries"][0].get("half") is not None:
        half_default = template["entries"][0]["half"]
    # written by hand in the game's own shape: no XML declaration, tab-indented, one SubTexture per line
    def attrs(d):
        return " ".join(f'{k}="{v}"' for k, v in d.items())
    lines = [f"<{tag} {attrs(attrib)}>"]
    for e in entries:
        a = {"name": e["name"], "x": str(e["x"]), "y": str(e["y"]), "width": str(e["width"]), "height": str(e["height"])}
        if half_default is not None:
            a["half"] = str(e.get("half", half_default))
        lines.append(f"\t<{child_tag} {attrs(a)}/>")
    lines.append(f"</{tag}>")
    return ("\n".join(lines) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------------------------------- images
def fit_icon(rgba: np.ndarray, size: int = ICON, margin: int = 8) -> np.ndarray:
    """Fit an RGBA image into a size x size transparent canvas (aspect kept, LANCZOS), centred."""
    from PIL import Image
    im = Image.fromarray(np.ascontiguousarray(rgba[..., :4] if rgba.shape[2] == 4 else _with_alpha(rgba)), "RGBA")
    inner = size - 2 * margin
    w, h = im.size
    s = min(inner / w, inner / h)
    im = im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(im, ((size - im.size[0]) // 2, (size - im.size[1]) // 2), im)
    return np.asarray(canvas)


def _with_alpha(rgb: np.ndarray) -> np.ndarray:
    a = np.full(rgb.shape[:2] + (1,), 255, np.uint8)
    return np.concatenate([rgb[..., :3], a], axis=2)


def pack_icons(icons: dict[int, np.ndarray], cell: int = ICON, pad: int = 0, sheet: int | None = None):
    """Lay icons out on one square RGBA sheet (power of two, smallest that fits). Returns (sheet, entries)."""
    n = len(icons)
    if sheet is None:
        sheet = 256
        while (sheet // (cell + pad)) ** 2 < n:
            sheet *= 2
    per_row = sheet // (cell + pad)
    out = np.zeros((sheet, sheet, 4), np.uint8)
    entries = []
    for k, (icon_id, img) in enumerate(sorted(icons.items())):
        if img.shape[0] != cell or img.shape[1] != cell:
            img = fit_icon(img, cell, margin=0)
        r, c = divmod(k, per_row)
        x, y = c * (cell + pad), r * (cell + pad)
        out[y:y + cell, x:x + cell] = img[..., :4] if img.shape[2] == 4 else _with_alpha(img)
        entries.append({"name": SUBTEXTURE_NAME.format(icon_id=icon_id), "x": x, "y": y, "width": cell, "height": cell})
    return out, entries


# ---------------------------------------------------------------------------------------------------- binders
def layouts_in(sblytbnd: bytes) -> dict[str, dict]:
    """stem -> parsed layout for every .layout entry of a (decompressed) sblytbnd."""
    bnd = BND4.read(sblytbnd)
    out = {}
    for f in bnd.files:
        if f.name.lower().endswith(".layout"):
            out[Path(f.name.replace("\\", "/")).stem] = parse_layout(f.data)
    return out


def find_icon(sblytbnd: bytes, icon_id: int):
    """(sheet stem, entry) of an icon id, or None."""
    name = SUBTEXTURE_NAME.format(icon_id=icon_id)
    for stem, lyt in layouts_in(sblytbnd).items():
        for e in lyt["entries"]:
            if e["name"] == name:
                return stem, e
    return None


def add_sheet(sblytbnd: bytes, tpf: bytes, stem: str, sheet_rgba: np.ndarray, entries: list[dict], fmt: str | None = None,
              mipmaps: bool | None = None) -> tuple[bytes, bytes]:
    """New sblytbnd + tpf (both raw, not DCX) with one more sheet. The layout's root element, the binder entry naming
    and the texture's TPF flags/format are copied from the existing icon sheets (SB_Icon_*)."""
    bnd = BND4.read(sblytbnd)
    layouts = [f for f in bnd.files if f.name.lower().endswith(".layout")]
    if not layouts:
        raise ValueError("no .layout entries in the sblytbnd")
    ref = next((f for f in layouts if "SB_Icon" in f.name), layouts[0])
    template = parse_layout(ref.data)
    xml = make_layout(stem, (sheet_rgba.shape[1], sheet_rgba.shape[0]), entries, template)
    new_name = ref.name.rsplit("\\", 1)[0] + "\\" + stem + ".layout"
    existing = bnd.get(stem + ".layout")
    if existing is not None:
        existing.data = xml
    else:   # appended, entry order kept (modded binders carry duplicate ids; BND4.set would re-sort them)
        bnd.files.append(BinderFile(max(f.id for f in bnd.files) + 1, new_name, xml, ref.flags))
    new_lyt = bnd.write()

    t = TPF.from_bytes(tpf)
    ref_tex = next((x for x in t.textures if x.stem.startswith("SB_Icon")), t.textures[0] if t.textures else None)
    ref_info = dds_tools.header_info(ref_tex.data) if ref_tex is not None else {}
    ref_fmt = ({b"DXT1": "DXT1", b"DXT5": "DXT5"}.get(ref_info.get("fourcc"))
               or ("BC7" if ref_info.get("dxgi_format") in (98, 99) else None))
    if fmt is None:
        fmt = ref_fmt or "DXT5"
    if mipmaps is None:
        mipmaps = ref_info.get("mipmaps", 1) > 1
    dds = bytearray(dds_tools.encode(sheet_rgba, fmt=fmt, mipmaps=mipmaps, srgb=False, dx10=(fmt == "BC7")))
    if ref_tex is not None:
        struct.pack_into("<I", dds, 24, struct.unpack_from("<I", ref_tex.data, 24)[0])   # dwDepth as the game writes it (1)
    # TPF format code: the reference sheet's when we encode the same way (Elden Ring: 102 = BC7), else the classic codes
    code = ref_tex.format if (ref_tex is not None and fmt == ref_fmt) else {"DXT1": 0, "DXT5": 5, "BC7": 102}[fmt]
    new_tex = TPFTexture(stem=stem, data=bytes(dds), format=code,
                         texture_flags=ref_tex.texture_flags if ref_tex is not None else 0,
                         texture_type=ref_tex.texture_type if ref_tex is not None else 0)
    t.textures = [x for x in t.textures if x.stem != stem] + [new_tex]
    return new_lyt, bytes(t)


def describe(sblytbnd: bytes, tpf: bytes | None = None) -> str:
    lines = []
    for stem, lyt in layouts_in(sblytbnd).items():
        ids = [int(m.group(1)) for e in lyt["entries"] for m in [re.match(r"MENU_ItemIcon_(\d+)\.png", e["name"] or "")] if m]
        lines.append(f"{stem}: {len(lyt['entries'])} icons, imagePath {lyt['image_path']}, root {lyt['tag']} {lyt['attrib']}"
                     + (f", item ids {min(ids)}..{max(ids)}" if ids else ""))
    if tpf is not None:
        t = TPF.from_bytes(tpf)
        lines.append(f"TPF: platform {t.platform.name} encoding {t.encoding_type} flags {t.tpf_flags}, {len(t.textures)} textures")
        for x in t.textures:
            if x.stem.startswith("SB_Icon"):
                info = dds_tools.header_info(x.data)
                lines.append(f"  {x.stem}: format {x.format} flags {x.texture_flags} type {int(x.texture_type)} "
                             f"{info.get('width')}x{info.get('height')} {info.get('fourcc')} dxgi {info.get('dxgi_format')} mips {info.get('mipmaps')}")
    return "\n".join(lines)


if __name__ == "__main__":
    import dcx
    args = sys.argv[1:]
    if not args:
        raise SystemExit("usage: er_icons.py 01_common.sblytbnd.dcx [01_common.tpf.dcx]")
    lyt = dcx.decompress(Path(args[0]).read_bytes())
    tp = dcx.decompress(Path(args[1]).read_bytes()) if len(args) > 1 else None
    print(describe(lyt, tp))
