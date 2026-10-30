# FromSoft modding reference (Dark Souls PTDE → Elden Ring)

Working notes gathered while porting a Dark Souls: Prepare to Die Edition weapon mod to Elden Ring.
Everything here was verified against real game files with the tools in `tools/`; where something is an
inference rather than a measurement it says so.

## 1. Where things live

### Dark Souls: Prepare to Die Edition (PTDE)
| Content | File | Format |
|---|---|---|
| Game params (weapons, bullets, behaviors, SpEffects…) | `param/GameParam/GameParam.parambnd` | BND3 `07D7R6`, uncompressed, 38 PARAMs (32-bit layout, row names in Shift-JIS; modded files usually carry English names) |
| Player TAEs + skeleton | `chr/c0000.anibnd` | BND3; `taeNew/win32/aNN.tae` (vanilla a00–a99), `hkxwin32/Skeleton.hkx` (hk2010 packfile, 61 bones). **No animation HKX in here.** |
| Player animation HKX | `chr/c0000_a0x.anibnd` … `c0000_a9x.anibnd`, `c0000_a00_lo/md/hi.anibnd`, `c0000_dlc.anibnd` | BND3; `aNN_MMMM.hkx` (hk2010 packfile, spline-compressed). Category NN·10000 + MMMM = full animation ID (`4310 → ref 1664300` = a166_4300). |
| Player state machine | `chr/c0000.esd` | EzState (`fSSL`). This is where input → animation logic lives in DS1 (the ER equivalent is `c0000.hks`). Editors save a readable `.esd.meta` XML next to it (declares UTF-16 but is UTF-8). |
| Shared enemy brain | `chr/enemyCommon.esd` | EzState |
| Weapon models | `parts/WP_A_NNNN.partsbnd` | FLVER + TPF; NNNN = `EquipParamWeapon.equipModelId` |

### Elden Ring
| Content | File | Format |
|---|---|---|
| All params | `regulation.bin` | AES-256-CBC (IV = first 16 bytes, key in `tools/er_regulation.py`), then DCX. Current patches use **DCX ZSTD** (older: DFLT). Inside: BND4 with 194 PARAMs (DLC-era). |
| Player TAEs + skeleton | `chr/c0000.anibnd.dcx` | DCX DFLT (`DCX_DFLT_10000_44_9`, not KRAK) → BND4; 687 TAEs a00–a999; `Skeleton.hkx` hk2018 tagfile, 150 bones |
| Player animation HKX | `chr/c0000_a0x…a9x.anibnd.dcx`, `c0000_a00_lo/md/hi` | `aNNN_MMMMMM.hkt`, hk2018 tagfiles |
| Player state machine | `action/script/c0000.hks` | Lua (plain text). Community "fixed decompile" (ividyon/EldenRingHKS) is the editable form. |
| Behavior graph | `chr/c0000.behbnd.dcx` | Havok behavior (HKB); only needed when adding clip generators for new animation IDs |

## 2. PARAM format notes
* **DS1 PARAM**: 32-bit; header 0x30; rows of 12 bytes (id, data off, name off). Paramdefs: Soulstruct bundles `paramdef.paramdefbnd` (format 104, 176-byte fields).
* **ER PARAM**: `flags2D` at 0x2D: `0x01` IntDataOffset, **`0x04` LongDataOffset (24-byte row entries: id, pad, u64 data, u64 name)**, `0x80` OffsetParamType. Header is 0x40 when either offset flag is set. Row names are absent in vanilla (use Paramdex `Names/*.txt`).
* **Bitfield packing** (both games): consecutive bitfields share a storage unit until the type changes or bits overflow; **`dummy8` bit-padding shares the byte with `u8` bits** (e.g. `u8 disableParam_NT:1` + `dummy8 reserve:7`). Getting this wrong shifts every later field by 1–2 bytes (EquipParamWeapon ER row = 664 bytes).
* Paramdex (Mar 2026) **mislabels SwordArtsParam offset 24**: it is really the u16 **art ID** (`swordArtsTypeNew` in HKS terms), not `shieldIconType`. `swordArtsType` at offset 4 is 0 for every row.

### Useful ER vanilla values
| | weaponCategory | wepmotionCategory | wepType | gemMountType | swordArtsParamId | behaviorVariationId | notes |
|---|---|---|---|---|---|---|---|
| Light Crossbow | 11 | 46 | 55 | 0 | 4990 (→ art 112 = Kick) | 4300 | `boltSlotEquipable`, `arrowBoltEquipId` 47010000 |
| Pulley / Full Moon / Spread Crossbow | 11 | 46 | 55 | 0 | 4990 | 4300 | `spAtkcategory` 233 / 236 / 235 |
| Shortbow / Longbow | 10 | 51 / 44 | 50 / 51 | 2 | 401 / 402 | 4000 / 4100 | |
| Dagger | 0 | 20 | 1 | 2 | 800 (Quickstep) | 100 | |
`gemMountType 2` = accepts Ashes of War; `spAtkcategory` accepts **50–999** (per paramdef), vanilla uses up to 236.
Free crossbow-range weapon IDs: 39 bases in 43100000–43490000 (step 10000). Free SwordArtsParam / EquipParamGem rows: 9100+.

## 3. TAE (Time Act) format
* DS1 PTDE: `TAE ` version `0x1000B`, 32-bit. Header: tae_id @0x50 (= 2000 + category), anim count @0x54, anim table @0x58 (id, off) pairs; anim struct = 7 ints (ev_count, ev_hdr_off, grp_count, grp_off, times_count, times_off, animfile_off); events = 3 ints (start_off, end_off, data_off); event data = (type i32, payload_off i32, payload).
* DSR / DS3 / ER: 64-bit (`0x1000C` / `0x1000D`); same structure with q-sized offsets; event type is i64.
* **Animation file struct**: flag byte at +0 (`is_reference`), name offset, then two ints. With `flags = 0x0100` the entry **imports another animation's HKX** by full ID (`unk1c` in Soulstruct's reader) — e.g. a166 anim 30 → 160033 = a16_0033. The gun mod stored all custom HKX under category a150 and imported them from a166–a181. ER TAEs do the same (`a46 37200 → 46037100`).
* `tools/tae_catalog.py` summarizes per-animation bullets / FFX / sounds / SpEffects; `tools/tae_template.py GAME [ids]` prints DSAnimStudio's event definitions.

### Event vocabulary (DS1 ↔ ER, same numbers)
0 ChrActionFlag (cancel/input windows), 1 AttackBehavior, 2 BulletBehavior (dummy poly, judge ID), 5 CommonBehavior, 16 Blend, 32 SetWeaponStyle, 66/67/401 AddSpEffect, 96 SpawnOneShotFFX, 112 FFX by floor, 128/129 PlaySound, 144/145 RumbleCam, 225 SP regen, 226 knockback, 232 torso aim (legacy in ER → use 238 SetBulletAimAngle / 707 ManualAttackAiming), 236 root-motion reduction, 307 PCBehavior.
Changed meaning: **300** (DS1 ChrActionGradient → ER ActivateJumpTableEarly). ER payloads are supersets (extra fields). ChrActionFlag IDs (4 RH cancel, 11 movement cancel, 25/26 dodge, 32 generic cancel, 87 common input…) are identical in both games; ER adds 103–121 (WA, quick-goods, R1/R2/L1/L2 cancels).
ER-only events of interest: 34/35 Un/EquipCrossbowBolt, 238 SetBulletAimAngle, 320 PlayerInputCheck, 609 playback conditioned on SpEffect, 700 EnableTwistModifier (torso aim), 902 ride turning, 712 OverrideWeaponModelLocations.

### ER crossbow slot families (TAE a46, 79 entries)
| IDs | Meaning (by event content) |
|---|---|
| 31030/31040/31050 | one-handed fire (loaded) — same HKX, turn-speed variants |
| 31060 | one-handed melee bash |
| 31070–31072, 31081–31082 | fire-from-unloaded (reload stitched in), **jump fire** + landings |
| 33xxx | two-handed mirror of all the above |
| 37000–37040 (+371xx, 373xx, 375xx, 376xx, 378xx) | **L1 aim stance**: fire / idle / transitions / reload (35 + 238) / exit; variants incl. **left hand** (judge 56, HandType 0) |
| 39700–39740 | **mounted** fire / idle / reload / exit (events 902, 712) |
| 200800, 2012x0, 201400 | misc / switch |
Bullets are spawned via behavior judge **55** (right) / **56** (left) → resolved through the equipped ammo's `behaviorVariationId`.

## 4. Ashes of War (verified in regulation + HKS)
* Gem (`EquipParamGem`) → `swordArtsParamId` → `SwordArtsParam` row → **art ID** (u16 @ offset 24) → **TAE a(600 + artID)** holding the skill animations (one TAE per art; 304 present a600–a999).
* Slot IDs inside the art TAE: 040000 start, 040005 start-without-FP, 040010/040015 follow-ups, 040050+ stance arts, 040080+ rolling; weapon-size variants are offsets (+200 large, +300 polearm, +5900 small shield…) chosen in HKS `GetSwordArtsDiffCategory`. Arts **not in the HKS table default to the single base animation** (nil-safe in the community HKS), and `SwordArtPutOppositeWeapon` is nil-safe too → a new art needs **no HKS edits**.
* `EquipParamGem.canMountWep_*` restricts by `wepType` (there is a **`canMountWep_ClossBow`** flag); the weapon needs `gemMountType 2`. FP cost: `SwordArtsParam.useMagicPoint_L2` (and `_R1/_R2` for follow-ups).
* Vanilla crossbows already have an art (Kick, 112), so the L2 path is live for category-46 weapons.
* Free art IDs: 26–49 (a626–a649 unused), 60, and most of 974–998.

## 5. Animation conversion (hk2010 → hk2018)
* Soulstruct-Havok (`vendor/`) reads DS1 packfiles, decompresses/recompresses spline data in pure Python, and `eldenring.AnimationHKX.from_2010_hkx()` converts the container; write with `dcx_type=DCXType.Null` for anibnd entries. Poses survive bit-exact (`tools/convert_anim_ds1_to_er.py --verify`).
* Patches in `vendor/patches/` fix: tagfile `hkStringPtr` items (`ConstCharArray`), dynamic `hkFlags[...]` type info, and add hk2010 `hkMemoryResourceContainer` & friends (Havok Content Tools exports carry an empty "Resource Data" variant; `tools/ds1_anim_load.py` strips it).
* Skeletons: DS1 c0000 = 61 bones, ER = 150; core chain is name-identical (Master, Pelvis, Spine, Spine1, Neck, Head, L/R Clavicle–UpperArm–Forearm–Hand, Finger0–2, L/R_Weapon/Shield). ER adds Spine2, Finger3/4, extra twist, IK-target, cloth and mantle bones; DS1 has Lower/Upper_Root and sheath (`Wepon_Case`) bones ER lacks. Retargeting = rest-pose-relative rotation copy + partial track binding (planned tool).
* Not convertible as data: FFX (DS1) → FXR (ER) effects, and FMOD → Wwise sounds. Reuse ER assets or re-author.

## 6. Environment notes
* Soulstruct needs Python ≥ 3.13; its optional deps (`rich`, `typer`, `zstandard`, `frozendict`) are shimmed in `vendor/shims/`.
* ER regulation ZSTD is decompressed through Node ≥ 22.15 (`zlib.zstdDecompressSync`); AES via Python `cryptography`.
* Oodle (DCX KRAK) is unavailable on Linux; so far nothing needed it (ER's player anibnd and regulation are DFLT/ZSTD).
