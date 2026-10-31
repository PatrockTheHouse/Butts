"""Build Elden Ring weapon/part models (FLVER 0x2001A + TPF + partsbnd) from plain mesh data, following the
conventions of vanilla parts binders (re-learned from reference files with `ERProfile.from_references`).

    import er_model as M
    profile = M.ERProfile.from_references([Path("wp_a_1503.partsbnd.dcx")], matbinbnd)   # or M.ERProfile.default()
    model = M.ERModel("WP_A_1580", profile, matbinbnd)
    mat = model.add_material("metal", matbin_stem="P[WP_A_1501]_metal",
                             textures={"AlbedoMap": "WP_A_1580_a", "NormalMap": "WP_A_1580_n", "MetallicMap": "WP_A_1580_m"})
    model.add_mesh(mat, positions, normals, uvs, triangles, backface_culling=False)
    model.add_dummy(100, (0, 0.6, 0.1), forward=(0, 1, 0), up=(-1, 0, 0))
    model.add_texture("WP_A_1580_a", rgba_array, kind="albedo")          # kinds: albedo, albedo_alpha, normal, metallic, emissive
    flver_bytes, tpf_bytes = model.build()                               # right-hand files
    flver_l, tpf_l = model.build(left=True)                              # "_L" variant (same FLVER, textures renamed *_l)
    M.pack_partsbnd(out_path, "WP_A_1580", flver_bytes, tpf_bytes, profile, left=False)

What the vanilla Elden Ring weapon binders look like (wp_a_1503 Light Crossbow, wp_a_1513 Full Moon Crossbow):
  - BND4 07D7R6 (format 0x74, hash table), entries 100 {model}.tpf, 200 {model}.flver (+400 anibnd, 800
    collidable hkx for rigged weapons); DCX KRAK (we write DFLT). The "_l" binder holds the identical FLVER
    (named {model}_L.flver) and a TPF whose textures are named {stem}_l (lower resolution in vanilla).
  - FLVER 0x2001A, header unk x4a=0 x4c=0 x5c=0 x5d=2 x68=4; a "Body" bone (usage 8 = used by mesh), a
    "Model_{model}"/"cXXXX" bone (usage 4) that is the mesh's default bone, a "Dmypoly_SFX" bone (usage 2) parenting
    the dummies, and unused bones (usage 1). Meshes: dynamic, no local bone list (vertex bone indices are global),
    layout Position/Normal(FourBytesB)/Tangent(FourBytesB)/BoneIndices/BoneWeights/Color(1)/UVPair, six face sets
    (flags 0, 1, 2 = LODs, 128/129/130 = the same three again), backface culling on. The SekiroUnkStruct after the
    texture headers is a bone hierarchy table (parent, child, next, prev) per bone, twice.
  - Material: name "0N - Default", mat_def_path N:\\GR\\data\\Material\\mtd\\Parts\\matxml\\{matbin}.matxml, flags
    1332 (C[DetailBlend]) / 480 (C[AMSN]_Glow), one GX00/102 item (40 bytes, generic), every matbin sampler listed
    with an empty path (texture names come from the matbin; we fill paths in to override them).
  - Dummies: parent Dmypoly_SFX, attach -1, follows_attach_bone False, unk_x30 1, unk_x34 290619284.
  - TPF: PC, encoding 1, flags 3; _a and _em BC1_UNORM_SRGB with DX10 headers (format code 0), _m BC4 (ATI1, 103),
    _n BC7_UNORM DX10 (106/107). Bullets fire from body dummy 55 (AttachmentType 2); weapon dummies are addressed
    as 10000+N (right) / 20000+N (left) in TAE events.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import modkit_env  # noqa: E402,F401
import numpy as np  # noqa: E402

import dcx  # noqa: E402
import dds_tools  # noqa: E402
from bnd4 import BND4, BinderFile  # noqa: E402
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
from soulstruct.utilities.maths import AABB, Vector2, Vector3  # noqa: E402

ER_INTERROOT = "N:\\GR\\data\\INTERROOT_win64"
MATXML_DIR = "N:\\GR\\data\\Material\\mtd\\Parts\\matxml"
TEXTURE_PATH = "N:\\GR\\data\\Model\\parts\\Weapon\\{model}\\tex\\{stem}.tif"
GX00_GENERIC = "000000000000000000000000ffffffff000000000000000000000000000000000000000000000000"

# TPFTexture.format codes (SoulsFormats/Soulstruct enum) by DDS pixel format.
TPF_FORMAT_BY_FOURCC = {b"DXT1": 0, b"DXT3": 3, b"DXT5": 5, b"ATI1": 103, b"BC4U": 103, b"ATI2": 104, b"BC5U": 104}
TPF_FORMAT_BY_DXGI = {71: 0, 72: 0, 74: 3, 75: 3, 77: 5, 78: 5, 80: 103, 83: 104, 95: 100, 98: 107, 99: 112, 28: 9, 29: 9}


def tpf_format_code(info: dict) -> int:
    if info["fourcc"] == b"DX10":
        return TPF_FORMAT_BY_DXGI.get(info["dxgi_format"], 107)
    return TPF_FORMAT_BY_FOURCC.get(info["fourcc"], 0)


# ------------------------------------------------------------------------------------------------------ profile

@dataclass
class ERProfile:
    """Format conventions for Elden Ring parts models; defaults are the values observed in vanilla weapon binders,
    `from_references` re-learns them from supplied files."""
    source: str = "built-in (vanilla wp_a_1503 / wp_a_1513 conventions)"
    header: dict = field(default_factory=lambda: {"f2_unk_x4a": False, "f2_unk_x4c": 0, "f2_unk_x5c": 0, "f2_unk_x5d": 2, "f2_unk_x68": 4})
    # bone set for a static part: (name template, usage flags); root-level bones chained as siblings like vanilla
    bones: list = field(default_factory=lambda: [["Body", 8], ["Model_{model}", 4], ["Dmypoly_SFX", 2], ["{model}", 1]])
    mesh_bone: str = "Body"               # vertices bind here
    default_bone: str = "Model_{model}"   # FLVERMesh.default_bone_index
    dummy_bone: str = "Dmypoly_SFX"       # dummies' parent bone
    dummy: dict = field(default_factory=lambda: {"attach_bone_index": -1, "follows_attach_bone": False, "use_upward_vector": True,
                                                 "unk_x30": 1, "unk_x34": 290619284, "color": [255, 255, 255, 255]})
    material: dict = field(default_factory=lambda: {"mat_def_path": MATXML_DIR + "\\{matbin}.matxml", "f2_unk_x18": 0,
                                                    "flags_by_shader": {"C[DetailBlend]": 1332, "C[AMSN]_Glow": 480, "C[AMSN]_Glow[Int]": 480},
                                                    "flags_default": 1332, "name_format": "{index:02d} - Default"})
    gx_items: list = field(default_factory=lambda: [["47583030", 102, GX00_GENERIC]])     # [category hex, index, data hex]
    texture_entry: dict = field(default_factory=lambda: {"with_path": {"scale": [1.0, 1.0], "f2_unk_x10": 1, "f2_unk_x11": True, "f2_unk_x14": 0.0, "f2_unk_x18": 0.0, "f2_unk_x1c": 0.0},
                                                         "no_path": {"scale": [1.0, 1.0], "f2_unk_x10": 0, "f2_unk_x11": False, "f2_unk_x14": 0.0, "f2_unk_x18": 0.0, "f2_unk_x1c": 0.0}})
    texture_path_format: str = TEXTURE_PATH
    mesh: dict = field(default_factory=lambda: {"is_dynamic": True, "face_set_flags": [0, 1, 2, 128, 129, 130], "unk_x06": 0,
                                                "normal_w": 127, "bbox": "true"})
    # texture kind -> (encode format, srgb, dx10)
    textures: dict = field(default_factory=lambda: {"albedo": ["BC1", True, True], "albedo_alpha": ["BC3", True, True],
                                                    "emissive": ["BC1", True, True], "metallic": ["BC4", False, False],
                                                    "normal": ["BC7", False, True], "default": ["BC7", False, True]})
    tpf: dict = field(default_factory=lambda: {"platform": "PC", "encoding_type": 1, "tpf_flags": 3, "texture_flags": 0, "texture_type": 0})
    binder: dict = field(default_factory=lambda: {"version": "07D7R6", "unk04": 0, "unk05": 0, "bit_big_endian": True, "unicode": True,
                                                  "raw_format": 0x74, "extended": 4, "dcx": "DFLT",
                                                  "entries": [{"id": 100, "name": ER_INTERROOT + "\\parts\\Weapon\\{model}\\{file}.tpf", "flags": 0x40},
                                                              {"id": 200, "name": ER_INTERROOT + "\\parts\\Weapon\\{model}\\{file}.flver", "flags": 0x40}]})
    reference: dict = field(default_factory=dict)   # what the reference files contained (informational)

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

    @classmethod
    def from_references(cls, partsbnd_paths: list[Path], matbinbnd: BND4 | None = None) -> "ERProfile":
        """Re-learn the conventions from vanilla partsbnd(.dcx) files (first file = primary)."""
        p = cls(source=", ".join(Path(x).name for x in partsbnd_paths))
        for i, path in enumerate(partsbnd_paths):
            raw = Path(path).read_bytes()
            bnd = BND4.read(dcx.decompress(raw))
            stem = Path(path).name.split(".")[0].upper().removesuffix("_L")
            flver_entry = next(e for e in bnd.files if e.name.lower().endswith(".flver"))
            tpf_entry = next((e for e in bnd.files if e.name.lower().endswith(".tpf")), None)
            fl = FLVER.from_bytes(flver_entry.data)
            info = {"bones": [[b.name, b.usage_flags, b.parent_bone_index] for b in fl.bones],
                    "dummies": [{"id": d.reference_id, "translate": [float(x) for x in d.translate], "forward": [float(x) for x in d.forward],
                                 "upward": [float(x) for x in d.upward], "parent": d.parent_bone_index, "attach": d.attach_bone_index,
                                 "follows": d.follows_attach_bone, "unk_x30": d.unk_x30, "unk_x34": d.unk_x34} for d in fl.dummies],
                    "materials": [], "meshes": [], "textures": [],
                    "binder": [[e.id, e.flags, e.name.replace(stem, "{model}")] for e in bnd.files]}
            if i == 0:
                p.header = {k: getattr(fl, k) for k in ("f2_unk_x4a", "f2_unk_x4c", "f2_unk_x5c", "f2_unk_x5d", "f2_unk_x68")}
                p.binder.update(version=bnd.version, unk04=bnd.unk04, unk05=bnd.unk05, bit_big_endian=bnd.bit_big_endian,
                                unicode=bnd.unicode, raw_format=bnd.raw_format, extended=bnd.extended)
                if fl.dummies:
                    d = fl.dummies[0]
                    p.dummy.update(attach_bone_index=d.attach_bone_index, follows_attach_bone=d.follows_attach_bone,
                                   use_upward_vector=d.use_upward_vector, unk_x30=d.unk_x30, unk_x34=d.unk_x34,
                                   color=[d.color.r, d.color.g, d.color.b, d.color.a])
                    if 0 <= d.parent_bone_index < len(fl.bones):
                        flags = fl.bones[d.parent_bone_index].usage_flags
                        p.dummy_bone = next((n for n, f in p.bones if f == flags), p.dummy_bone)
                me = fl.meshes[0]
                p.mesh.update(is_dynamic=me.is_dynamic, face_set_flags=sorted({fs.flags for fs in me.face_sets}),
                              unk_x06=me.face_sets[0].unk_x06 if me.face_sets else 0)
                if me.vertex_arrays and "normal_w" in me.vertex_arrays[0].array.dtype.names:
                    p.mesh["normal_w"] = int(me.vertex_arrays[0].array["normal_w"][0][0])
                # Map the reference's default/mesh bones onto our generic bone set by usage flags (names differ per model).
                if 0 <= me.default_bone_index < len(fl.bones):
                    flags = fl.bones[me.default_bone_index].usage_flags
                    p.default_bone = next((n for n, f in p.bones if f == flags), p.default_bone)
                used = set(int(x) for x in np.unique(me.vertex_arrays[0].array["bone_indices"])) if me.vertex_arrays else {0}
                flags = fl.bones[min(used)].usage_flags
                p.mesh_bone = next((n for n, f in p.bones if f == flags), p.mesh_bone)
            mats = {}
            for me in fl.meshes:
                mats.setdefault(id(me.material), me.material)
            for m in mats.values():
                matbin_stem = PureWindowsPath(m.mat_def_path).stem
                shader = None
                if matbinbnd is not None:
                    f = matbinbnd.get(matbin_stem + ".matbin")
                    if f:
                        shader = MATBIN.from_bytes(f.data).shader_stem
                        p.material["flags_by_shader"][shader] = m.flags
                p.material["mat_def_path"] = m.mat_def_path.replace(matbin_stem, "{matbin}")
                p.material["f2_unk_x18"] = m.f2_unk_x18
                if i == 0:
                    p.material["flags_default"] = m.flags
                    p.gx_items = [[g.category.hex(), g.index, g.data.hex()] for g in m.gx_items if not g.is_terminator] or p.gx_items
                    for t in m.textures:
                        key = "with_path" if t.path else "no_path"
                        p.texture_entry[key] = {"scale": [float(t.scale[0]), float(t.scale[1])], "f2_unk_x10": t.f2_unk_x10, "f2_unk_x11": t.f2_unk_x11,
                                                "f2_unk_x14": t.f2_unk_x14, "f2_unk_x18": t.f2_unk_x18, "f2_unk_x1c": t.f2_unk_x1c}
                        if t.path:
                            tex_stem = PureWindowsPath(t.path).stem
                            p.texture_path_format = t.path.replace(tex_stem, "{stem}").replace(stem, "{model}")
                info["materials"].append({"name": m.name, "matbin": matbin_stem, "shader": shader, "flags": m.flags,
                                          "gx": [[g.category.decode("ascii", "replace"), g.index, g.data.hex()] for g in m.gx_items],
                                          "textures": [[t.texture_type, t.path] for t in m.textures]})
            for me in fl.meshes:
                info["meshes"].append({"default_bone": me.default_bone_index, "bone_indices": None if me.bone_indices is None else [int(x) for x in me.bone_indices],
                                       "layout": [repr(t) for t in me.vertex_arrays[0].layout.read_types] if me.vertex_arrays else [],
                                       "face_sets": [[fs.flags, int(len(fs.vertex_indices)), fs.use_backface_culling, fs.unk_x06] for fs in me.face_sets],
                                       "bbox": [[float(x) for x in me.bounding_box.min], [float(x) for x in me.bounding_box.max]],
                                       "bbox_unknown": [float(x) for x in me.bounding_box_unknown] if me.bounding_box_unknown is not None else None,
                                       "vertex_min": [float(x) for x in me.vertex_arrays[0].array["position"].min(0)] if me.vertex_arrays else None,
                                       "vertex_max": [float(x) for x in me.vertex_arrays[0].array["position"].max(0)] if me.vertex_arrays else None})
            if tpf_entry is not None:
                tpf = TPF.from_bytes(tpf_entry.data)
                if i == 0:
                    p.tpf.update(platform=tpf.platform.name, encoding_type=tpf.encoding_type, tpf_flags=tpf.tpf_flags,
                                 texture_flags=tpf.textures[0].texture_flags if tpf.textures else 0,
                                 texture_type=int(tpf.textures[0].texture_type) if tpf.textures else 0)
                for t in tpf.textures:
                    h = dds_tools.header_info(t.data)
                    suffix = t.stem.removesuffix("_l").rsplit("_", 1)[-1]
                    kind = {"a": "albedo", "em": "emissive", "m": "metallic", "n": "normal"}.get(suffix)
                    fmt = {b"DXT1": "BC1", b"DXT5": "BC3", b"ATI1": "BC4", b"BC4U": "BC4", b"ATI2": "BC5"}.get(h["fourcc"])
                    if h["fourcc"] == b"DX10":
                        fmt = {71: "BC1", 72: "BC1", 77: "BC3", 78: "BC3", 80: "BC4", 83: "BC5", 98: "BC7", 99: "BC7"}.get(h["dxgi_format"])
                    srgb = h["dxgi_format"] in (72, 75, 78, 99) if h["fourcc"] == b"DX10" else False
                    if kind and fmt and i == 0:
                        p.textures[kind] = [fmt, srgb, h["fourcc"] == b"DX10"]
                    info["textures"].append({"stem": t.stem, "format": t.format, "fourcc": h["fourcc"].decode("ascii", "replace"),
                                             "dxgi": h["dxgi_format"], "size": [h["width"], h["height"]], "mips": h["mipmaps"]})
            p.reference[Path(path).name] = info
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
    textures: dict
    material: Material


class ERModel:
    def __init__(self, model_stem: str, profile: ERProfile, matbinbnd: BND4 | None = None):
        self.stem = model_stem
        self.profile = profile
        self.matbinbnd = matbinbnd
        self.flver = FLVER(version=FLVERVersion.Sekiro_EldenRing, unicode=True, **profile.header)
        names = [n.replace("{model}", model_stem) for n, _ in profile.bones]
        for i, ((_, flags), name) in enumerate(zip(profile.bones, names)):
            self.flver.bones.append(FLVERBone(name=name, parent_bone_index=-1, child_bone_index=-1, usage_flags=flags,
                                              next_sibling_bone_index=i + 1 if i + 1 < len(names) else -1,
                                              previous_sibling_bone_index=i - 1))
        self.bone_index = {n: i for i, n in enumerate(names)}
        self.mesh_bone = self.bone_index[profile.mesh_bone.replace("{model}", model_stem)]
        self.default_bone = self.bone_index[profile.default_bone.replace("{model}", model_stem)]
        self.dummy_bone = self.bone_index[profile.dummy_bone.replace("{model}", model_stem)]
        self.materials: list[_Mat] = []
        self.textures: dict[str, tuple[np.ndarray, str]] = {}
        self._all_positions = []

    # ------------------------------------------------------------------------------------------------ materials
    def matbin(self, matbin_stem: str) -> MATBIN | None:
        if self.matbinbnd is None:
            return None
        f = self.matbinbnd.get(matbin_stem + ".matbin")
        return MATBIN.from_bytes(f.data) if f else None

    def add_material(self, name: str, matbin_stem: str, textures: dict[str, str]) -> _Mat:
        """`textures` maps a sampler fragment ('AlbedoMap', 'NormalMap', 'MetallicMap', 'EmissiveMap' or a full
        sampler type) to a texture stem of this model. Every matbin sampler is listed (vanilla style); the ones we
        override get a path, which takes precedence over the matbin's own texture."""
        p = self.profile
        mb = self.matbin(matbin_stem)
        if mb is None:
            raise ValueError(f"matbin {matbin_stem} not found (matbinbnd needed to enumerate samplers)")
        resolved = {}
        for key in textures:
            exact = [s for s in mb.samplers if s.sampler_type == key]
            cands = [s for s in (exact or [s for s in mb.samplers if key in s.sampler_type]) if s.sampler_type not in resolved.values()]
            if not cands:
                raise ValueError(f"{matbin_stem}: no sampler matches {key!r}; samplers: {[s.sampler_type for s in mb.samplers]}")
            primary = [s for s in cands if s.path]
            resolved[key] = (primary or cands)[0].sampler_type
        by_type = {resolved[k]: stem for k, stem in textures.items()}
        entries = []
        for s in mb.samplers:
            stem = by_type.get(s.sampler_type)
            fields = dict(p.texture_entry["with_path" if stem else "no_path"])
            scale = fields.pop("scale")
            path = p.texture_path_format.replace("{model}", self.stem).replace("{stem}", stem) if stem else ""
            entries.append(Texture(path=path, texture_type=s.sampler_type, scale=Vector2(scale), **fields))
        flags = p.material["flags_by_shader"].get(mb.shader_stem, p.material["flags_default"])
        gx = [GXItem(bytes.fromhex(c), i, bytes.fromhex(d)) for c, i, d in p.gx_items] + [GXItem.new_terminator()]
        mat_name = p.material.get("name_format", "{name}").format(index=len(self.materials) + 7, name=name)
        mat = Material(name=mat_name, mat_def_path=p.material["mat_def_path"].replace("{matbin}", matbin_stem),
                       textures=entries, flags=flags, f2_unk_x18=p.material["f2_unk_x18"], gx_items=gx)
        m = _Mat(name, matbin_stem, textures, mat)
        self.materials.append(m)
        return m

    # ------------------------------------------------------------------------------------------------ geometry
    def vertex_layout(self, mat: _Mat, uv_count: int = 2) -> VertexArrayLayout:
        """Dynamic (parts) layout; UV count from the matbin's shader when Soulstruct knows it."""
        mb = self.matbin(mat.matbin_stem)
        if mb is not None:
            try:
                from soulstruct.eldenring.models.shaders import MatDef
                return MatDef.from_matbin(mb).get_vertex_array_layout(is_dynamic=True)
            except Exception as e:  # noqa: BLE001
                print(f"  note: MatDef layout for {mat.matbin_stem} unavailable ({e}); using the generic parts layout")
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

    def add_mesh(self, mat: _Mat, positions, normals, uvs, triangles, colors=None, backface_culling: bool = True,
                 layout: VertexArrayLayout | None = None, tangents=None) -> FLVERMesh:
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
        arr["position"] = positions
        arr["normal"] = normals
        if "normal_w" in arr.dtype.names:
            arr["normal_w"] = p.mesh.get("normal_w", 127)
        for name in arr.dtype.names:
            if name.startswith("tangent_"):
                arr[name] = tangents
            elif name.startswith("uv_"):
                arr[name] = uvs if arr[name].shape[1:] == uvs.shape[1:] else uvs[:, :arr[name].shape[1]]
            elif name.startswith("color_"):
                arr[name] = np.asarray(colors, np.float32) if colors is not None else 1.0
            elif name == "bone_indices":
                arr[name] = self.mesh_bone
            elif name == "bone_weights":
                arr[name] = np.array([1.0, 0.0, 0.0, 0.0], np.float32)
            elif name == "bitangent":
                arr[name] = np.concatenate([np.cross(normals, tangents[:, :3]) * tangents[:, 3:4], np.ones((n, 1), np.float32)], axis=1)
        face_sets = [FaceSet(flags=f, is_triangle_strip=False, use_backface_culling=backface_culling, unk_x06=p.mesh.get("unk_x06", 0),
                             vertex_indices=triangles.copy()) for f in p.mesh.get("face_set_flags", [0])]
        lo, hi = positions.min(0), positions.max(0)
        mesh = FLVERMesh(is_dynamic=p.mesh.get("is_dynamic", True), material=mat.material, default_bone_index=self.default_bone,
                         bone_indices=None, vertex_arrays=[VertexArray(arr, layout)], face_sets=face_sets,
                         bounding_box=AABB(Vector3(lo), Vector3(hi)), bounding_box_unknown=Vector3((lo + hi) / 2))
        self.flver.meshes.append(mesh)
        self._all_positions.append(positions)
        return mesh

    def add_dummy(self, reference_id: int, translate, forward=(0, 1, 0), up=(-1, 0, 0), **overrides) -> Dummy:
        d = dict(self.profile.dummy)
        d.update(overrides)
        color = d.pop("color")
        dummy = Dummy(translate=Vector3(translate), forward=Vector3(forward), upward=Vector3(up), reference_id=reference_id,
                      parent_bone_index=self.dummy_bone, color=ColorRGBA(*color), **d)
        self.flver.dummies.append(dummy)
        return dummy

    def add_texture(self, stem: str, rgba: np.ndarray, kind: str = "default"):
        """`rgba` (H, W, 4) uint8; `kind` selects the encoding policy (albedo, albedo_alpha, emissive, metallic, normal)."""
        self.textures[stem] = (np.ascontiguousarray(rgba, dtype=np.uint8), kind)

    # ------------------------------------------------------------------------------------------------ output
    def _finish_flver(self):
        fl = self.flver
        if self._all_positions:
            allp = np.concatenate(self._all_positions)
            fl.bounding_box = AABB(Vector3(allp.min(0)), Vector3(allp.max(0)))
            for b in fl.bones:
                b.bounding_box = AABB(Vector3(allp.min(0)), Vector3(allp.max(0)))
        hierarchy = [((b.parent_bone_index, b.child_bone_index, b.next_sibling_bone_index, b.previous_sibling_bone_index), i)
                     for i, b in enumerate(fl.bones)]
        fl.sekiro_unk_members1 = list(hierarchy)
        fl.sekiro_unk_members2 = list(hierarchy)

    def build(self, left: bool = False) -> tuple[bytes, bytes]:
        """(flver bytes, tpf bytes). The left-hand variant has the same FLVER and textures named {stem}_l."""
        self._finish_flver()
        flver_bytes = bytes(self.flver)
        tp = self.profile.tpf
        tpf = TPF(platform=TPFPlatform[tp["platform"]], encoding_type=tp["encoding_type"], tpf_flags=tp["tpf_flags"])
        if not hasattr(self, "_encoded"):
            self._encoded = {}
        for stem, (rgba, kind) in self.textures.items():
            if stem not in self._encoded:
                fmt, srgb, dx10 = self.profile.textures.get(kind, self.profile.textures["default"])
                self._encoded[stem] = dds_tools.encode(rgba, fmt, mipmaps=True, srgb=srgb, dx10=dx10)
            dds = self._encoded[stem]
            info = dds_tools.header_info(dds)
            tpf.textures.append(TPFTexture(stem=stem + ("_l" if left else ""), data=dds, format=tpf_format_code(info),
                                           texture_flags=tp["texture_flags"], texture_type=tp["texture_type"], mipmap_count=info["mipmaps"]))
        return flver_bytes, bytes(tpf)


def pack_partsbnd(out_path: Path, model_stem: str, flver_bytes: bytes, tpf_bytes: bytes, profile: ERProfile, left: bool = False) -> bytes:
    b = profile.binder
    bnd = BND4(version=b["version"], unk04=b["unk04"], unk05=b["unk05"], bit_big_endian=b["bit_big_endian"], unicode=b["unicode"],
               raw_format=b["raw_format"], extended=b["extended"])
    file_stem = model_stem + ("_L" if left else "")
    for e in b["entries"]:
        name = e["name"].replace("{model}", model_stem).replace("{file}", file_stem)
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

def ds1_textures_to_er(diffuse: bytes | None, specular: bytes | None, bump: bytes | None, glow: bytes | None = None) -> dict[str, tuple[np.ndarray, str]]:
    """DS1 DDS texture set -> ER RGBA arrays keyed by suffix: {'_a': (rgba, kind), '_n': ..., '_m': ..., '_em': ...}.

    _a  = diffuse (kind albedo, or albedo_alpha when the diffuse carries alpha).
    _n  = R,G from the bump map; B = gloss, A = specular (both from the specular map's brightness).
    _m  = metallic = specular brightness (single channel).
    _em = glow map.
    """
    out = {}
    if diffuse is not None:
        d = dds_tools.decode(diffuse)
        has_alpha = dds_tools.header_info(diffuse)["fourcc"] in (b"DXT5", b"DXT3") or bool((d[..., 3] < 250).any())
        out["_a"] = (d, "albedo_alpha" if has_alpha else "albedo")
    spec_gray = dds_tools.decode(specular)[..., :3].mean(axis=2) if specular is not None else None
    if bump is not None:
        n = dds_tools.decode(bump).copy()
        g = spec_gray
        if g is not None and g.shape != n.shape[:2]:
            from PIL import Image
            g = np.asarray(Image.fromarray(g.astype(np.uint8)).resize((n.shape[1], n.shape[0]), Image.Resampling.LANCZOS))
        n[..., 2] = np.clip(g, 0, 255).astype(np.uint8) if g is not None else 128
        n[..., 3] = np.clip(g, 0, 255).astype(np.uint8) if g is not None else 128
        out["_n"] = (n, "normal")
    if spec_gray is not None:
        m = np.empty(spec_gray.shape + (4,), np.uint8)
        m[..., 0] = m[..., 1] = m[..., 2] = np.clip(spec_gray, 0, 255).astype(np.uint8)
        m[..., 3] = 255
        out["_m"] = (m, "metallic")
    if glow is not None:
        out["_em"] = (dds_tools.decode(glow), "emissive")
    return out
