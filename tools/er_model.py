"""Build Elden Ring weapon/part models (FLVER 0x2001A + TPF + partsbnd) from plain mesh data, with the
format details learned from a vanilla reference partsbnd.

    import er_model as M
    profile = M.ERProfile.from_reference(Path("wp_a_1503.partsbnd.dcx"))   # or M.ERProfile.default()
    model = M.ERModel("WP_A_1580", profile)
    mat = model.add_material("metal", matbin_stem="P[WP_A_1501]_metal",
                             textures={"AlbedoMap": "WP_A_1580_a", "NormalMap": "WP_A_1580_n", "MetallicMap": "WP_A_1580_m"})
    model.add_mesh(mat, positions, normals, uvs, triangles, colors=None, backface_culling=False)
    model.add_dummy(55, (0, 0.6, 0.1), forward=(0, 1, 0), up=(-1, 0, 0))
    model.add_texture("WP_A_1580_a", dds_bytes)
    flver_bytes, tpf_bytes = model.build()
    M.pack_partsbnd(Path("out/wp_a_1580.partsbnd.dcx"), "WP_A_1580", flver_bytes, tpf_bytes, profile)

Meshes are static (every vertex bound to bone 0 with weight 1) in the dynamic/character vertex layout Elden Ring
uses for parts; tangents are generated from the UVs. Materials point at vanilla matbins (no matbinbnd edits):
the FLVER texture paths override the matbin's sampler paths. `ERProfile` holds everything copied from the
reference (header unknowns, bone/dummy conventions, per-matbin GX items, texture entry fields, face set flags,
TPF/DDS formats, binder layout); `ERProfile.default()` is the best guess used until a reference is supplied.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import modkit_env  # noqa: E402,F401
import numpy as np  # noqa: E402

import dds_tools  # noqa: E402
import dcx  # noqa: E402
from bnd4 import BND4, BinderFile  # noqa: E402
from bnd_tools import parse_binder  # noqa: E402
from soulstruct.base.models.matbin import MATBIN  # noqa: E402
from soulstruct.containers.tpf import TPF, TPFTexture, TPFPlatform  # noqa: E402
from soulstruct.flver import FLVER, FLVERVersion  # noqa: E402
from soulstruct.flver.bone import FLVERBone  # noqa: E402
from soulstruct.flver.dummy import Dummy, ColorRGBA  # noqa: E402
from soulstruct.flver.face_set import FaceSet  # noqa: E402
from soulstruct.flver.gx_item import GXItem  # noqa: E402
from soulstruct.flver.material import Material  # noqa: E402
from soulstruct.flver.mesh import FLVERMesh  # noqa: E402
from soulstruct.flver.texture import Texture  # noqa: E402
from soulstruct.flver.vertex_array import VertexArray  # noqa: E402
from soulstruct.flver.vertex_array_layout import (  # noqa: E402
    VertexArrayLayout, VertexBoneIndices, VertexBoneWeights, VertexColor, VertexDataFormatEnum, VertexNormal,
    VertexPosition, VertexTangent, VertexUV)
from soulstruct.utilities.maths import AABB, EulerRad, Vector2, Vector3  # noqa: E402

ER_INTERROOT = "N:\\GR\\data\\INTERROOT_win64"
MATXML_DIR = "N:\\GR\\data\\Material\\mtd\\Parts\\matxml"


# ------------------------------------------------------------------------------------------------------ profile

@dataclass
class ERProfile:
    """Format conventions copied from a vanilla Elden Ring parts FLVER/TPF/partsbnd."""
    source: str = "default (guessed)"
    header: dict = field(default_factory=lambda: {"f2_unk_x4a": True, "f2_unk_x4c": 0, "f2_unk_x5c": 0, "f2_unk_x5d": 0, "f2_unk_x68": 4})
    # bone templates: name with the reference model stem replaced by "{model}"
    bones: list = field(default_factory=lambda: [{"name": "{model}", "parent": -1, "child": -1, "next": -1, "prev": -1, "usage_flags": 0}])
    dummy: dict = field(default_factory=lambda: {"parent_bone_index": -1, "attach_bone_index": 0, "follows_attach_bone": True,
                                                 "use_upward_vector": True, "unk_x30": 0, "unk_x34": 0, "color": [255, 255, 255, 255]})
    reference_dummies: list = field(default_factory=list)       # as found in the reference (for information)
    material: dict = field(default_factory=lambda: {"flags": 0, "f2_unk_x18": 0, "mat_def_path": MATXML_DIR + "\\{matbin}.matxml"})
    gx_items: dict = field(default_factory=dict)                 # matbin stem -> [[category(hex), index, data(hex)], ...]
    texture_entry: dict = field(default_factory=lambda: {"with_path": {"scale": [1.0, 1.0], "f2_unk_x10": 1, "f2_unk_x11": True, "f2_unk_x14": 0.0, "f2_unk_x18": 0.0, "f2_unk_x1c": 0.0},
                                                         "no_path": {"scale": [1.0, 1.0], "f2_unk_x10": 0, "f2_unk_x11": False, "f2_unk_x14": 0.0, "f2_unk_x18": 0.0, "f2_unk_x1c": 0.0}})
    texture_path_format: str = ER_INTERROOT + "\\parts\\Weapon\\{model}\\tex\\{stem}.tif"
    all_samplers_listed: bool = True                             # FLVER lists every matbin sampler (empty path when unused)
    mesh: dict = field(default_factory=lambda: {"is_dynamic": True, "default_bone_index": 0, "bone_indices": "used",
                                                "bounding_box_unknown": "center", "face_set_flags": [0], "unk_x06": 0,
                                                "vertex_index_bit_size": 16, "normal_w": 127})
    tpf: dict = field(default_factory=lambda: {"platform": "PC", "encoding_type": 1, "tpf_flags": 3, "texture_flags": 0, "texture_type": 0,
                                               "formats": {},                       # reference stems -> format codes (informational)
                                               "dds": {"dx10": False, "srgb": {"_a": True, "_em": True, "_m": False, "_n": False}}})
    binder: dict = field(default_factory=lambda: {"version": "07D7R6", "unk04": 0, "unk05": 0, "bit_big_endian": True, "unicode": True,
                                                  "raw_format": 0x74, "extended": 4, "dcx": "DFLT",
                                                  "entries": [{"id": 100, "name": ER_INTERROOT + "\\parts\\Weapon\\{model}\\{model}.tpf", "flags": 0x40},
                                                              {"id": 200, "name": ER_INTERROOT + "\\parts\\Weapon\\{model}\\{model}.flver", "flags": 0x40}]})

    # ---------------------------------------------------------------------------------------------- persistence
    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=1)

    @classmethod
    def from_json(cls, text: str) -> "ERProfile":
        p = cls()
        p.__dict__.update(json.loads(text))
        return p

    @classmethod
    def default(cls) -> "ERProfile":
        return cls()

    # ---------------------------------------------------------------------------------------------- learn
    @classmethod
    def from_reference(cls, partsbnd_path: Path, model_stem: str | None = None) -> "ERProfile":
        """Learn the conventions from a vanilla partsbnd(.dcx). `model_stem` (e.g. 'WP_A_1503') defaults to the
        binder file name."""
        raw = Path(partsbnd_path).read_bytes()
        dcx_kind = dcx.kind(raw)
        buf, hdr, ents = parse_binder(raw)
        bnd = BND4.read(dcx.decompress(raw))
        stem = model_stem or Path(partsbnd_path).name.split(".")[0].upper()
        p = cls(source=str(partsbnd_path))
        flver_entry = next(e for e in bnd.files if e.name.lower().endswith(".flver"))
        tpf_entry = next((e for e in bnd.files if e.name.lower().endswith(".tpf")), None)
        fl = FLVER.from_bytes(flver_entry.data)

        p.header = {k: getattr(fl, k) for k in ("f2_unk_x4a", "f2_unk_x4c", "f2_unk_x5c", "f2_unk_x5d", "f2_unk_x68")}
        p.bones = [{"name": b.name.replace(stem, "{model}"), "parent": b.parent_bone_index, "child": b.child_bone_index,
                    "next": b.next_sibling_bone_index, "prev": b.previous_sibling_bone_index, "usage_flags": b.usage_flags,
                    "translate": [float(x) for x in b.translate], "rotate": [float(x) for x in b.rotate],
                    "scale": [float(x) for x in b.scale]} for b in fl.bones]
        p.reference_dummies = [{"reference_id": d.reference_id, "translate": [float(x) for x in d.translate],
                                "forward": [float(x) for x in d.forward], "upward": [float(x) for x in d.upward],
                                "parent_bone_index": d.parent_bone_index, "attach_bone_index": d.attach_bone_index,
                                "follows_attach_bone": d.follows_attach_bone, "use_upward_vector": d.use_upward_vector,
                                "unk_x30": d.unk_x30, "unk_x34": d.unk_x34, "color": [d.color.r, d.color.g, d.color.b, d.color.a]}
                               for d in fl.dummies]
        if fl.dummies:
            d = fl.dummies[0]
            p.dummy = {"parent_bone_index": d.parent_bone_index, "attach_bone_index": d.attach_bone_index,
                       "follows_attach_bone": d.follows_attach_bone, "use_upward_vector": d.use_upward_vector,
                       "unk_x30": d.unk_x30, "unk_x34": d.unk_x34, "color": [d.color.r, d.color.g, d.color.b, d.color.a]}
        mats = {}
        for me in fl.meshes:
            mats.setdefault(id(me.material), me.material)
        with_path = no_path = None
        for m in mats.values():
            matbin_stem = Path(m.mat_def_path.replace("\\", "/")).stem
            p.material = {"flags": m.flags, "f2_unk_x18": m.f2_unk_x18,
                          "mat_def_path": m.mat_def_path.replace(matbin_stem, "{matbin}")}
            p.gx_items[matbin_stem] = [[g.category.hex(), g.index, g.data.hex()] for g in m.gx_items]
            for t in m.textures:
                entry = {"scale": [float(t.scale[0]), float(t.scale[1])], "f2_unk_x10": t.f2_unk_x10, "f2_unk_x11": t.f2_unk_x11,
                         "f2_unk_x14": t.f2_unk_x14, "f2_unk_x18": t.f2_unk_x18, "f2_unk_x1c": t.f2_unk_x1c}
                if t.path and with_path is None:
                    with_path = entry
                    p.texture_path_format = t.path.replace(stem, "{model}")
                    tex_stem = Path(t.path.replace("\\", "/")).stem
                    p.texture_path_format = t.path.replace(tex_stem, "{stem}").replace(stem, "{model}")
                elif not t.path and no_path is None:
                    no_path = entry
            p.all_samplers_listed = any(not t.path for t in m.textures)
        if with_path:
            p.texture_entry["with_path"] = with_path
        if no_path:
            p.texture_entry["no_path"] = no_path
        me = fl.meshes[0]
        bb_unknown = "center" if me.bounding_box_unknown is not None else None
        p.mesh = {"is_dynamic": me.is_dynamic, "default_bone_index": me.default_bone_index,
                  "bone_indices": "used" if me.bone_indices is not None and len(me.bone_indices) < len(fl.bones) else "all",
                  "bounding_box_unknown": bb_unknown, "face_set_flags": sorted({fs.flags for fs in me.face_sets}),
                  "unk_x06": me.face_sets[0].unk_x06 if me.face_sets else 0, "vertex_index_bit_size": 16,
                  "normal_w": int(me.vertex_arrays[0].array["normal_w"][0][0]) if me.vertex_arrays and "normal_w" in me.vertex_arrays[0].array.dtype.names else 127,
                  "reference_layout": [repr(t) for t in me.vertex_arrays[0].layout.read_types] if me.vertex_arrays else [],
                  "reference_face_sets": [{"flags": fs.flags, "indices": int(len(fs.vertex_indices)), "strip": fs.is_triangle_strip,
                                           "culling": fs.use_backface_culling, "unk_x06": fs.unk_x06} for fs in me.face_sets],
                  "reference_bone_indices": [int(x) for x in me.bone_indices] if me.bone_indices is not None else None,
                  "reference_bbox_unknown": [float(x) for x in me.bounding_box_unknown] if me.bounding_box_unknown is not None else None,
                  "reference_bbox": [[float(x) for x in me.bounding_box.min], [float(x) for x in me.bounding_box.max]]}
        if tpf_entry is not None:
            tpf = TPF.from_bytes(tpf_entry.data)
            formats = {}
            dx10 = False
            srgb = {}
            for t in tpf.textures:
                suffix = "_" + t.stem.rsplit("_", 1)[-1] if "_" in t.stem else "default"
                formats[suffix] = t.format
                info = dds_tools.header_info(t.data)
                if info["fourcc"] == b"DX10":
                    dx10 = True
                    srgb[suffix] = info["dxgi_format"] in (72, 75, 78, 99, 29)
            p.tpf = {"platform": tpf.platform.name, "encoding_type": tpf.encoding_type, "tpf_flags": tpf.tpf_flags,
                     "texture_flags": tpf.textures[0].texture_flags if tpf.textures else 0,
                     "texture_type": int(tpf.textures[0].texture_type) if tpf.textures else 0,
                     "formats": formats | {"default": formats.get("_a", 0)}, "dds": {"dx10": dx10, "srgb": srgb},
                     "reference_textures": [{"stem": t.stem, "format": t.format, "flags": t.texture_flags, "mips": t.mipmap_count,
                                             "dds": {k: (v.decode("ascii", "replace") if isinstance(v, bytes) else v)
                                                     for k, v in dds_tools.header_info(t.data).items()}} for t in tpf.textures]}
        p.binder = {"version": bnd.version, "unk04": bnd.unk04, "unk05": bnd.unk05, "bit_big_endian": bnd.bit_big_endian,
                    "unicode": bnd.unicode, "raw_format": bnd.raw_format, "extended": bnd.extended,
                    "dcx": dcx_kind.decode() if dcx_kind else None,
                    "entries": [{"id": e.id, "name": e.name.replace(stem, "{model}"), "flags": e.flags} for e in bnd.files]}
        return p


# ------------------------------------------------------------------------------------------------------ geometry

def compute_tangents(positions: np.ndarray, normals: np.ndarray, uvs: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Per-vertex tangents (x, y, z, w) from UV derivatives (Lengyel); w = bitangent handedness (+1/-1)."""
    n = len(positions)
    tan1 = np.zeros((n, 3))
    tan2 = np.zeros((n, 3))
    p0, p1, p2 = (positions[triangles[:, i]].astype(float) for i in range(3))
    t0, t1, t2 = (uvs[triangles[:, i]].astype(float) for i in range(3))
    e1, e2 = p1 - p0, p2 - p0
    d1, d2 = t1 - t0, t2 - t0
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(det) > 1e-12, 1.0 / np.where(np.abs(det) > 1e-12, det, 1.0), 0.0)
    sdir = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) * r[:, None]
    tdir = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) * r[:, None]
    for i in range(3):
        np.add.at(tan1, triangles[:, i], sdir)
        np.add.at(tan2, triangles[:, i], tdir)
    nrm = normals.astype(float)
    t = tan1 - nrm * np.sum(nrm * tan1, axis=1, keepdims=True)
    length = np.linalg.norm(t, axis=1, keepdims=True)
    # Vertices without UV area: pick any vector perpendicular to the normal.
    fallback = np.cross(nrm, np.array([0.0, 1.0, 0.0]))
    degenerate = np.linalg.norm(fallback, axis=1) < 1e-8
    fallback[degenerate] = np.cross(nrm[degenerate], np.array([1.0, 0.0, 0.0]))
    bad = length[:, 0] < 1e-8
    t[bad] = fallback[bad]
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
    w = np.where(np.sum(np.cross(nrm, t) * tan2, axis=1) < 0.0, -1.0, 1.0)
    return np.concatenate([t, w[:, None]], axis=1).astype(np.float32)


def rotate_180_about(axis: str, v: np.ndarray) -> np.ndarray:
    """Rotate vectors 180 degrees about a principal axis ('x', 'y', 'z')."""
    out = -np.asarray(v, dtype=np.float32)
    i = "xyz".index(axis[-1])
    out[:, i] = -out[:, i]
    return out


# ------------------------------------------------------------------------------------------------------ builder

@dataclass
class _Mat:
    name: str
    matbin_stem: str
    textures: dict            # sampler-type substring -> texture stem (looked up in the matbin's sampler names)
    material: Material


class ERModel:
    def __init__(self, model_stem: str, profile: ERProfile, matbinbnd: BND4 | None = None):
        self.stem = model_stem
        self.profile = profile
        self.matbinbnd = matbinbnd
        self.flver = FLVER(version=FLVERVersion.Sekiro_EldenRing, unicode=True, **profile.header)
        for bt in profile.bones:
            self.flver.bones.append(FLVERBone(
                name=bt["name"].replace("{model}", model_stem), parent_bone_index=bt["parent"], child_bone_index=bt["child"],
                next_sibling_bone_index=bt["next"], previous_sibling_bone_index=bt["prev"], usage_flags=bt.get("usage_flags", 0),
                translate=Vector3(bt.get("translate", (0, 0, 0))), rotate=EulerRad(bt.get("rotate", (0, 0, 0))),
                scale=Vector3(bt.get("scale", (1, 1, 1)))))
        self.materials: list[_Mat] = []
        self.textures: dict[str, bytes] = {}
        self._all_positions = []

    # ------------------------------------------------------------------------------------------------ materials
    def matbin(self, matbin_stem: str) -> MATBIN | None:
        if self.matbinbnd is None:
            return None
        f = self.matbinbnd.get(matbin_stem + ".matbin")
        return MATBIN.from_bytes(f.data) if f else None

    def add_material(self, name: str, matbin_stem: str, textures: dict[str, str]) -> _Mat:
        """`textures` maps a sampler-name fragment ('AlbedoMap', 'NormalMap', 'MetallicMap', 'EmissiveMap', or a full
        sampler type) to a texture stem in this model's TPF. Samplers are enumerated from the matbin when available
        (all listed, unused ones with empty paths, as in vanilla FLVERs)."""
        p = self.profile
        mb = self.matbin(matbin_stem)
        tex_entries = []
        resolved = {}

        def entry(sampler_type: str, stem: str | None) -> Texture:
            fields = dict(p.texture_entry["with_path" if stem else "no_path"])
            scale = fields.pop("scale")
            path = p.texture_path_format.replace("{model}", self.stem).replace("{stem}", stem) if stem else ""
            return Texture(path=path, texture_type=sampler_type, scale=Vector2(scale), **fields)

        if mb is not None:
            # Resolve each key to one sampler: exact type name, else the samplers containing the fragment, preferring
            # the one the matbin itself gives a texture (the shader's primary slot of that kind).
            for key, tex_stem in textures.items():
                exact = [s for s in mb.samplers if s.sampler_type == key]
                cands = exact or [s for s in mb.samplers if key in s.sampler_type]
                cands = [s for s in cands if s.sampler_type not in resolved.values()]
                if not cands:
                    raise ValueError(f"{matbin_stem}: no sampler matches {key!r}; samplers: {[s.sampler_type for s in mb.samplers]}")
                primary = [s for s in cands if s.path]
                resolved[key] = (primary or cands)[0].sampler_type
            by_type = {resolved[k]: tex_stem for k, tex_stem in textures.items()}
            for s in mb.samplers:
                stem = by_type.get(s.sampler_type)
                if stem or p.all_samplers_listed:
                    tex_entries.append(entry(s.sampler_type, stem))
        else:
            for key, tex_stem in textures.items():
                tex_entries.append(entry(key, tex_stem))
        gx = [GXItem(bytes.fromhex(c), i, bytes.fromhex(d)) for c, i, d in p.gx_items.get(matbin_stem, [])]
        if not gx:
            gx = [GXItem.new_terminator()]
        mat = Material(name=name, mat_def_path=p.material["mat_def_path"].replace("{matbin}", matbin_stem),
                       textures=tex_entries, flags=p.material["flags"], f2_unk_x18=p.material["f2_unk_x18"], gx_items=gx)
        m = _Mat(name, matbin_stem, textures, mat)
        self.materials.append(m)
        return m

    # ------------------------------------------------------------------------------------------------ geometry
    def vertex_layout(self, mat: _Mat, uv_count: int = 1) -> VertexArrayLayout:
        """Dynamic (parts) layout; UV count from the matbin's samplers when known, else `uv_count`."""
        mb = self.matbin(mat.matbin_stem)
        if mb is not None:
            try:
                from soulstruct.eldenring.models.shaders import MatDef
                matdef = MatDef.from_matbin(mb)
                return matdef.get_vertex_array_layout(is_dynamic=True)
            except Exception as e:  # noqa: BLE001 - fall back to the generic layout
                print(f"  note: MatDef layout for {mat.matbin_stem} unavailable ({e}); using generic layout")
        types = [VertexPosition(VertexDataFormatEnum.Float3, 0), VertexNormal(VertexDataFormatEnum.FourBytesB, 0),
                 VertexTangent(VertexDataFormatEnum.FourBytesB, 0), VertexBoneIndices(VertexDataFormatEnum.FourBytesB, 0),
                 VertexBoneWeights(VertexDataFormatEnum.FourBytesC, 0), VertexColor(VertexDataFormatEnum.FourBytesC, 1)]
        i = 0
        while uv_count > 0:
            if uv_count >= 2:
                types.append(VertexUV(VertexDataFormatEnum.UVPair, i)); uv_count -= 2
            else:
                types.append(VertexUV(VertexDataFormatEnum.UV, i)); uv_count -= 1
            i += 1
        return VertexArrayLayout(types)

    def add_mesh(self, mat: _Mat, positions, normals, uvs, triangles, colors=None, backface_culling: bool = False,
                 bone_index: int = 0, layout: VertexArrayLayout | None = None, tangents=None) -> FLVERMesh:
        p = self.profile
        positions = np.asarray(positions, np.float32)
        normals = np.asarray(normals, np.float32)
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
        uvs = np.asarray(uvs, np.float32)
        triangles = np.asarray(triangles, np.uint32).reshape(-1, 3)
        if tangents is None:
            tangents = compute_tangents(positions, normals, uvs, triangles)
        layout = layout or self.vertex_layout(mat)
        _, dtype = layout.get_dtypes()
        n = len(positions)
        arr = np.zeros(n, dtype=dtype)
        names = arr.dtype.names
        arr["position"] = positions
        arr["normal"] = normals
        if "normal_w" in names:
            arr["normal_w"] = p.mesh.get("normal_w", 127)
        for name in names:
            if name.startswith("tangent_"):
                arr[name] = tangents
            elif name.startswith("uv_"):
                arr[name] = uvs if arr[name].shape[1:] == uvs.shape[1:] else uvs[:, :arr[name].shape[1]]
            elif name.startswith("color_"):
                arr[name] = np.asarray(colors, np.float32) if colors is not None else 1.0
            elif name == "bone_indices":
                arr[name] = bone_index
            elif name == "bone_weights":
                arr[name] = np.array([1.0, 0.0, 0.0, 0.0], np.float32)
            elif name == "bitangent":
                arr[name] = np.concatenate([np.cross(normals, tangents[:, :3]) * tangents[:, 3:4], np.ones((n, 1), np.float32)], axis=1)
        face_sets = [FaceSet(flags=f, is_triangle_strip=False, use_backface_culling=backface_culling, unk_x06=p.mesh.get("unk_x06", 0),
                             vertex_indices=triangles.copy()) for f in p.mesh.get("face_set_flags", [0])]
        bb = AABB(Vector3(positions.min(0)), Vector3(positions.max(0)))
        unk = Vector3((positions.min(0) + positions.max(0)) / 2) if p.mesh.get("bounding_box_unknown") == "center" else None
        bone_indices = np.array([bone_index], np.int32) if p.mesh.get("bone_indices") == "used" else np.arange(len(self.flver.bones), dtype=np.int32)
        mesh = FLVERMesh(is_dynamic=p.mesh.get("is_dynamic", True), material=mat.material, default_bone_index=p.mesh.get("default_bone_index", 0),
                         bone_indices=bone_indices, vertex_arrays=[VertexArray(arr, layout)], face_sets=face_sets,
                         bounding_box=bb, bounding_box_unknown=unk)
        self.flver.meshes.append(mesh)
        self._all_positions.append(positions)
        return mesh

    def add_dummy(self, reference_id: int, translate, forward=(0, 1, 0), up=(-1, 0, 0), **overrides) -> Dummy:
        d = dict(self.profile.dummy)
        d.update(overrides)
        color = d.pop("color")
        dummy = Dummy(translate=Vector3(translate), forward=Vector3(forward), upward=Vector3(up), reference_id=reference_id,
                      color=ColorRGBA(*color), **d)
        self.flver.dummies.append(dummy)
        return dummy

    def add_texture(self, stem: str, dds: bytes):
        self.textures[stem] = dds

    # ------------------------------------------------------------------------------------------------ output
    def build(self) -> tuple[bytes, bytes]:
        fl = self.flver
        if self._all_positions:
            allp = np.concatenate(self._all_positions)
            fl.bounding_box = AABB(Vector3(allp.min(0)), Vector3(allp.max(0)))
            for b in fl.bones:
                b.bounding_box = AABB(Vector3(allp.min(0)), Vector3(allp.max(0)))
        flver_bytes = bytes(fl)
        tp = self.profile.tpf
        tpf = TPF(platform=TPFPlatform[tp["platform"]], encoding_type=tp["encoding_type"], tpf_flags=tp["tpf_flags"])
        for stem, dds in self.textures.items():
            suffix = "_" + stem.rsplit("_", 1)[-1]
            data = dds
            if tp["dds"].get("dx10"):
                data = dds_tools.to_dx10(dds, srgb=tp["dds"].get("srgb", {}).get(suffix, False))
            info = dds_tools.header_info(data)
            tpf.textures.append(TPFTexture(stem=stem, data=data, format=tpf_format_code(info), texture_flags=tp["texture_flags"],
                                           texture_type=tp["texture_type"], mipmap_count=info["mipmaps"]))
        return flver_bytes, bytes(tpf)


# TPFTexture.format codes (SoulsFormats/Soulstruct enum) by DDS pixel format.
TPF_FORMAT_BY_FOURCC = {b"DXT1": 0, b"DXT3": 3, b"DXT5": 5, b"ATI1": 103, b"ATI2": 104}
TPF_FORMAT_BY_DXGI = {71: 0, 72: 0, 74: 3, 75: 3, 77: 5, 78: 5, 80: 103, 83: 104, 95: 100, 98: 102, 99: 112, 28: 9, 29: 9}


def tpf_format_code(info: dict) -> int:
    if info["fourcc"] == b"DX10":
        return TPF_FORMAT_BY_DXGI.get(info["dxgi_format"], 102)
    return TPF_FORMAT_BY_FOURCC.get(info["fourcc"], 0)


def pack_partsbnd(out_path: Path, model_stem: str, flver_bytes: bytes, tpf_bytes: bytes, profile: ERProfile) -> bytes:
    b = profile.binder
    bnd = BND4(version=b["version"], unk04=b["unk04"], unk05=b["unk05"], bit_big_endian=b["bit_big_endian"], unicode=b["unicode"],
               raw_format=b["raw_format"], extended=b["extended"])
    for e in b["entries"]:
        name = e["name"].replace("{model}", model_stem)
        low = name.lower()
        data = flver_bytes if low.endswith(".flver") else tpf_bytes if low.endswith(".tpf") else b""
        if not data:
            continue
        bnd.files.append(BinderFile(e["id"], name, data, e.get("flags", 0x40)))
    bnd.files.sort(key=lambda f: f.id)
    raw = bnd.write()
    out = dcx.compress_dflt(raw) if b.get("dcx") else raw
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(out)
    return out


# ------------------------------------------------------------------------------------------------------ textures

def ds1_textures_to_er(diffuse: bytes | None, specular: bytes | None, bump: bytes | None, glow: bytes | None = None,
                       alpha: bool = False) -> dict[str, bytes]:
    """DS1 texture set -> ER set: {'_a': dds, '_n': dds, '_m': dds[, '_em': dds]}.

    _a  = diffuse as-is (DXT1, or DXT5 when `alpha`).
    _n  = R,G from the bump map; B = gloss (specular brightness); A = specular (specular brightness). DXT5.
    _m  = metallic: specular brightness in RGB (DXT1).
    _em = glow map as-is.
    """
    out = {}
    if diffuse is not None:
        out["_a"] = diffuse
    spec_rgba = dds_tools.decode(specular) if specular is not None else None
    spec_gray = spec_rgba[..., :3].mean(axis=2) if spec_rgba is not None else None
    if bump is not None:
        n = dds_tools.decode(bump).copy()
        if spec_gray is not None:
            g = spec_gray
            if g.shape != n.shape[:2]:
                from PIL import Image
                g = np.asarray(Image.fromarray(g.astype(np.uint8)).resize((n.shape[1], n.shape[0]), Image.Resampling.LANCZOS))
            n[..., 2] = np.clip(g, 0, 255).astype(np.uint8)
            n[..., 3] = np.clip(g, 0, 255).astype(np.uint8)
        else:
            n[..., 2] = 128
            n[..., 3] = 128
        out["_n"] = dds_tools.encode(n, "DXT5")
    if spec_rgba is not None:
        m = np.empty_like(spec_rgba)
        m[..., 0] = m[..., 1] = m[..., 2] = np.clip(spec_gray, 0, 255).astype(np.uint8)
        m[..., 3] = 255
        out["_m"] = dds_tools.encode(m, "DXT1")
    if glow is not None:
        out["_em"] = glow
    return out
