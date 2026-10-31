"""Dump the structure of a FLVER model (DS1 … Elden Ring), standalone or inside a partsbnd/chrbnd binder.

    flver_info.py FILE [--verts] [--json OUT.json]

FILE may be a .flver, or a binder (.partsbnd[.dcx], .chrbnd[.dcx], …) in which case every FLVER and TPF entry
is reported. Prints header fields, bones, dummies, materials with textures and GX items, meshes with their vertex
layouts and face sets, the vertex extents per mesh, and the TPF textures (format, size, DDS fourcc). --verts also
prints the first vertices of each mesh. --json writes the same information as JSON (for conversion tooling).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import modkit_env  # noqa: E402,F401
import numpy as np  # noqa: E402

from bnd_tools import parse_binder  # noqa: E402
from soulstruct.flver import FLVER  # noqa: E402
from soulstruct.containers.tpf import TPF  # noqa: E402


def _v(x):
    return [float(round(float(c), 5)) for c in x]


def flver_summary(fl: FLVER) -> dict:
    out = {
        "version": fl.version.name, "version_hex": hex(int(fl.version)), "unicode": fl.unicode, "big_endian": fl.big_endian,
        "bbox_min": _v(fl.bounding_box.min), "bbox_max": _v(fl.bounding_box.max),
        "unk": {"x4a": fl.f2_unk_x4a, "x4c": fl.f2_unk_x4c, "x5c": fl.f2_unk_x5c, "x5d": fl.f2_unk_x5d, "x68": fl.f2_unk_x68},
        "bones": [], "dummies": [], "materials": [], "meshes": [],
    }
    for i, b in enumerate(fl.bones):
        out["bones"].append({
            "index": i, "name": b.name, "parent": b.parent_bone_index, "child": b.child_bone_index,
            "next_sibling": b.next_sibling_bone_index, "prev_sibling": b.previous_sibling_bone_index,
            "translate": _v(b.translate), "rotate": _v(b.rotate), "scale": _v(b.scale), "usage_flags": b.usage_flags,
            "bbox_min": _v(b.bounding_box.min), "bbox_max": _v(b.bounding_box.max),
        })
    for d in fl.dummies:
        out["dummies"].append({
            "reference_id": d.reference_id, "translate": _v(d.translate), "forward": _v(d.forward), "upward": _v(d.upward),
            "parent_bone": d.parent_bone_index, "attach_bone": d.attach_bone_index, "follows_attach_bone": d.follows_attach_bone,
            "use_upward_vector": d.use_upward_vector, "color": list(d.color) if hasattr(d.color, "__iter__") else str(d.color),
            "unk": {k: getattr(d, k) for k in ("unk_x30", "unk_x34", "f2_unk_x30", "f2_unk_x34") if hasattr(d, k)},
        })
    mat_index = {}
    for me in fl.meshes:
        mat_index.setdefault(id(me.material), len(mat_index))
    mats = {}
    for me in fl.meshes:
        mats[id(me.material)] = me.material
    for mid, m in mats.items():
        out["materials"].append({
            "index": mat_index[mid], "name": m.name, "mat_def_path": m.mat_def_path, "flags": m.flags, "unk_x18": m.f2_unk_x18,
            "gx_items": [{"category": g.category.decode("ascii", "replace"), "index": g.index, "size": len(g.data), "data": g.data.hex()}
                         for g in m.gx_items],
            "textures": [{"type": t.texture_type, "path": t.path, "scale": _v(t.scale), "unk_x10": t.f2_unk_x10, "unk_x11": t.f2_unk_x11,
                          "unk_x14": t.f2_unk_x14, "unk_x18": t.f2_unk_x18, "unk_x1c": t.f2_unk_x1c} for t in m.textures],
        })
    for i, me in enumerate(fl.meshes):
        arr = me.vertex_arrays[0].array if me.vertex_arrays else None
        pos = arr["position"] if arr is not None and len(arr) and "position" in arr.dtype.names else None
        out["meshes"].append({
            "index": i, "material": mat_index[id(me.material)], "is_dynamic": me.is_dynamic, "default_bone": me.default_bone_index,
            "bone_indices": [int(x) for x in (me.bone_indices if me.bone_indices is not None else [])],
            "bbox_min": _v(me.bounding_box.min), "bbox_max": _v(me.bounding_box.max),
            "bbox_unknown": _v(me.bounding_box_unknown) if me.bounding_box_unknown is not None else None,
            "vertex_count": int(len(arr)) if arr is not None else 0,
            "layout": [repr(t) for t in me.vertex_arrays[0].layout.read_types] if me.vertex_arrays else [],
            "fields": list(arr.dtype.names) if arr is not None else [],
            "vertex_min": _v(pos.min(0)) if pos is not None else None, "vertex_max": _v(pos.max(0)) if pos is not None else None,
            "face_sets": [{"flags": fs.flags, "indices": int(len(fs.vertex_indices)), "triangle_strip": fs.is_triangle_strip,
                           "backface_culling": fs.use_backface_culling} for fs in me.face_sets],
        })
    return out


def tpf_summary(tpf: TPF) -> dict:
    out = {"platform": tpf.platform.name, "encoding_type": tpf.encoding_type, "tpf_flags": tpf.tpf_flags, "textures": []}
    for t in tpf.textures:
        info = {"stem": t.stem, "format": t.format, "texture_flags": t.texture_flags, "texture_type": int(t.texture_type),
                "mipmaps": t.mipmap_count, "bytes": len(t.data)}
        try:
            dds = t.get_dds()
            h = dds.header
            info.update(fourcc=t.get_dds_fourcc().decode("ascii", "replace"), width=h.width, height=h.height, dds_mipmaps=h.mipmap_count,
                        dx10=dds.dx10_header is not None if hasattr(dds, "dx10_header") else None)
            if getattr(dds, "dx10_header", None) is not None:
                info["dxgi_format"] = str(dds.dx10_header.dxgi_format)
        except Exception as e:  # noqa: BLE001
            info["dds_error"] = str(e)
        out["textures"].append(info)
    return out


def print_flver(name: str, s: dict, verts=False, fl: FLVER = None):
    u = s["unk"]
    print(f"== {name}: FLVER {s['version']} ({s['version_hex']}) unicode={s['unicode']} bbox {s['bbox_min']} .. {s['bbox_max']}")
    print(f"   unk x4a={u['x4a']} x4c={u['x4c']} x5c={u['x5c']} x5d={u['x5d']} x68={u['x68']}")
    for b in s["bones"]:
        print(f"   bone {b['index']:>3} {b['name']!r:<28} parent {b['parent']:>3} child {b['child']:>3} sib {b['next_sibling']:>3}/{b['prev_sibling']:>3}"
              f" t={b['translate']} r={b['rotate']} s={b['scale']} flags={b['usage_flags']}")
    for d in s["dummies"]:
        print(f"   dummy {d['reference_id']:>4} at {d['translate']} fwd {d['forward']} up {d['upward']} parent {d['parent_bone']} attach {d['attach_bone']}"
              f" follow={d['follows_attach_bone']} useup={d['use_upward_vector']} color={d['color']} {d['unk']}")
    for m in s["materials"]:
        print(f"   material {m['index']} {m['name']!r} | {m['mat_def_path']} | flags {m['flags']} unk18 {m['unk_x18']}")
        for g in m["gx_items"]:
            print(f"      gx {g['category']!r} {g['index']} [{g['size']} bytes] {g['data'][:64]}{'…' if len(g['data']) > 64 else ''}")
        for t in m["textures"]:
            print(f"      tex {t['type']:<48} {t['path']!r} scale {t['scale']} unks {t['unk_x10']} {t['unk_x11']} {t['unk_x14']} {t['unk_x18']} {t['unk_x1c']}")
    for i, me in enumerate(s["meshes"]):
        print(f"   mesh {me['index']} mat {me['material']} dyn={me['is_dynamic']} default_bone {me['default_bone']} bones {me['bone_indices']}"
              f" verts {me['vertex_count']} bbox {me['bbox_min']} .. {me['bbox_max']} unk {me['bbox_unknown']}")
        print(f"      layout {me['layout']}")
        print(f"      extents {me['vertex_min']} .. {me['vertex_max']}  face sets {me['face_sets']}")
        if verts and fl is not None and fl.meshes[i].vertex_arrays:
            arr = fl.meshes[i].vertex_arrays[0].array
            with np.printoptions(precision=4, suppress=True, linewidth=200):
                for row in arr[:3]:
                    print("      ", {n: row[n] for n in arr.dtype.names})


def print_tpf(name: str, s: dict):
    print(f"== {name}: TPF platform {s['platform']} encoding {s['encoding_type']} flags {s['tpf_flags']}")
    for t in s["textures"]:
        extra = f" {t.get('fourcc')} {t.get('width')}x{t.get('height')} dds_mips {t.get('dds_mipmaps')}" + (f" dx10 {t.get('dxgi_format')}" if t.get("dx10") else "")
        print(f"   {t['stem']:<32} format {t['format']:>3} flags {t['texture_flags']} type {t['texture_type']} mips {t['mipmaps']:>2} {t['bytes']:>9} bytes{extra}{t.get('dds_error', '')}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    verts = "--verts" in sys.argv
    json_out = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
    path = Path(args[0])
    raw = path.read_bytes()
    report = {"file": str(path), "entries": []}
    items = []
    if raw[:4] in (b"BND3", b"BND4", b"DCX\x00"):
        buf, hdr, ents = parse_binder(raw)
        print(f"{path.name}: {hdr['kind']} {hdr.get('version')} with {len(ents)} entries")
        for e in ents:
            data = buf[e["offset"]:e["offset"] + e["size"]]
            print(f"   {e['id']:>6} {e['size']:>9} {e['name']}")
            items.append((e["name"].split("\\")[-1], e["id"], data))
    else:
        items.append((path.name, None, raw))
    for name, eid, data in items:
        if data[:5] == b"FLVER":
            fl = FLVER.from_bytes(data)
            s = flver_summary(fl)
            print_flver(name, s, verts, fl)
            report["entries"].append({"name": name, "id": eid, "flver": s})
        elif data[:3] == b"TPF":
            tpf = TPF.from_bytes(data)
            s = tpf_summary(tpf)
            print_tpf(name, s)
            report["entries"].append({"name": name, "id": eid, "tpf": s})
    if json_out:
        Path(json_out).write_text(json.dumps(report, indent=1))
        print(f"wrote {json_out}")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    main()
