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
* **Writing ER PARAMs** (`er_param.encode_row` / `rebuild_param`): a new row is its template row's bytes with the
  named fields patched (bitfields read-modify-write, arrays as list or `|`-string, fixstr padded); the param is
  rebuilt as header (stringsOffset u32@0, rowCount u16@0xA, DataStart u64@0x30 updated) + 24-byte entries sorted by
  id + tight data block + the original strings block (param type name + shared empty row name; name offsets shifted).
  Rebuilding 192/194 vanilla params this way is byte-identical; the other two differ only in padding.
* **Writing `regulation.bin`** (`er_regulation.write_regulation`): BND4 → `dcx.compress_zstd` (Node
  `zlib.zstdCompressSync`, level 15 like the shipped file; header `DCX 0x11000 … DCP ZSTD 0x20 level`) → PKCS7 →
  AES-256-CBC with a random IV prefixed. The game loads the result (same layout as the shipped file).
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

### ER TAE (0x1000D) layout rules — `tools/tae3.py` (byte-identical on all 687 vanilla player TAEs)
Header 0xD0; strings (skeleton `skeleton.hkt`, sib `c9990.SIB`) padded to 16; animation table (q id, q offset);
animation *groups* = maximal runs of consecutive animation ids (count, entries `<iiQ>` first id, last id, table
offset); 48-byte animation structs; per animation: file record `<QQQii>` (type, +16, name offset, a, b) + 16-byte
tail, UTF-16 name (padded to 16 only when non-empty), unique sorted float32 times (padded), event headers `<QQQ>`
(start-time ptr, end-time ptr, data ptr), event data `<qQ>` (type, payload ptr) + payload (contiguous), event
groups `<QQQQ>` (count, index ptr, type ptr, 0) + per group `<QQ>` (type, 0) and i32 header offsets.
Payload sizes vary per instance (padded); the last payload is ambiguous by 8 bytes → `data/er_tae_event_sizes.json`
(measured modal sizes) decides. Event-group "families" (`tae3.GROUP_FAMILY`): 34/35→32, 119→96, 129→128, 238→232,
605→605… Animation file record: type 0 = standard (a = flags, `0x100` imports the HKX of animation b = cat·1,000,000 +
id, name still spelled `aXXX_YYYYYY.hkt`); type 1 = reference to another animation's events (a = full id).
Cross-category imports are normal (a235 Spread Crossbow imports `46033030` from a46); partial override TAEs
(`spAtkcategory`) define only the animations they change, the rest falls back to `wepmotionCategory`.
`tools/tae_events.py` encodes/decodes payloads by DSAnimStudio field names; `tools/tae_dump.py` prints them.

### ER player TAE numbering (from a modded c0000.anibnd: 621 TAEs)
* Files `aNN.tae` for 0–99 (weapon motion categories, e.g. a46 crossbow) and `aNNN.tae` above; binder entry id
  5000000 + N, internal TAE id 2000 + N. Weapon rows: `wepmotionCategory` = base TAE (a046), `spAtkcategory` =
  override TAE consulted first (vanilla: 100–293 for every weapon variant, 831–953 for specials, Clever's modpack
  678–761/857/859; the HKS can read it with `GetEquipWeaponSpecialCategoryNumber`).
* **a300–a599 are not free**: the engine reads a(N + 300) as an overlay of TAE N for whole animation classes
  (a3xx hold 90xxx clips, a4xx/a5xx hold 45xxx/47xxx/48xxx/145xxx clips — 1478 of their 1487 animations exist in
  no base TAE). A custom TAE numbered 573 is therefore only ever consulted as "a273 + 300", never as a
  `spAtkcategory` (symptom: the weapon silently plays its base category's moveset). Same rule as the DS3 tutorial
  "only add TAE ids in a000–a299 or a600–a999". Ashes of War use a(600 + artID); a9xx are standalone (Repeating
  Crossbow 935, Rakshasa 953) — custom weapon TAEs fit in 974–998 or the 7xx/8xx gaps.
* **The TAE does not choose the clip — the behavior graph does** (next section). An animation record names its
  clip (`a046_037030.hkt`, own-type) or imports it by id (`file_b` = category × 1000000 + anim when
  `file_a & 0x100`); 482 vanilla own-type records and 5337 imports point at *other* categories (a000 uses
  `a663_040010.hkt`, a235 imports `46033030`). An override TAE whose category the graph does not know plays its
  **events** over the base category's **clips**: fire rates and reloads change, the motion never does, and even a
  byte copy of a vanilla clip filed under the new category will not play. (Earlier notes here blamed div placement,
  clip naming and a46 registration for that symptom; all of it was the graph.) The arrangement that mirrors vanilla
  a235 → 46033030 is still what the gun TAEs use: clips in the a4x div **named a046_9xxxxx** (unused ids), an
  event-less own-type record for each in a46.tae, imported from the override TAE (`file_b` 46903110). Divs hold
  whole categories (2-digit: tens digit → a4x holds a040–a049; a233/a236's clips are not in a4x). Clips placed in
  the main c0000.anibnd are not consulted; a compendium (`TCRF`) clip there crashes the boot.
* Real vanilla player clips (compendium `TCRF` files) animate only the bones they need (`a046_037130`: 57 tracks,
  `transformTrackToBoneIndices` partial, `maskAndQuantizationSize` 228), have one root variant and no reference
  frame; the 150-track "Scene Data"/"Resource Data" self-contained clips in modded divs are modder-made (3ds Max
  2019 + HCT 2018) and load fine. Soulstruct rewrites a vanilla clip with a byte-identical DATA section.

### ER behavior graph (`chr/c0000.behbnd.dcx`) — the clip selection authority (`tools/er_behavior.py`)
* BND4 of three hk2018 tagfiles: `Export\c0000.hkx` (project), `Export\Characters\c0000.hkx`, and
  `Export\Behaviors\c0000.hkx` (17.6 MB, ids 7000300/7000400/7000500; entries are raw TAG0, not DCX-wrapped).
  A modded (Clever's) graph: 120 978 items, 30 328 `hkbClipGenerator`, 4 059 `CustomManualSelectorGenerator`
  (CMSG), 4 316 `hkbVariableBindingSet`. The div re-saved as DCX DFLT loads.
* **Every animation state ends in a CMSG** with `animId` (+172, e.g. 37110) and `offsetType` (+168: 11 idle
  category, 13 right-hand weapon category, 14 left hand, 15 anim id, 16 hand style + weapon category, 17 magic,
  18 sword arts — HkbEditor's enum). Its `generators` (+152, hkArray of `hkbGenerator*`) are clip generators, **one
  per weapon category that has the animation**: crossbow raise 37110 has `a046_037110`, `a044_…`, `a052_…`,
  `a235_…`. The engine picks the child whose category matches the weapon (`spAtkcategory` first — Spread Crossbow
  46/235, Pulley 46/233, Full Moon 46/236, Repeating 46/935 are the stock examples — then `wepmotionCategory`); a
  category with no child falls back to the base category's clip while the TAE events still come from the override
  TAE. The chosen name is then resolved through that category's TAE record (own name or import) to the HKX.
* `hkbClipGenerator` (352 bytes): `variableBindingSet` +24 (hkRefPtr; the 1H attack variants bind `startTime` to a
  variable), `name` +72, `animationName` +152 (hkStringPtr, short form `a046_037110`), `animationInternalId` +198
  (hkInt16). Base-game clips: internal id = index of the full path
  `..\..\..\..\Model\chr\c0000\hkx\a046\a046_037110.hkx` in `hkbBehaviorGraphStringData.animationNames` (14 893
  entries, field +88); the **DLC categories (a083–a116) and Clever's (a678–a953) are not in that list** and use ids
  past its end (up to 20 926, four even colliding with list indices), so the list is not consulted for new clips.
  FromSoft's DLC clips use one string for `name` and `animationName`; base-game names carry `.hkx` / `_hkx_AutoSet_NN`.
* **Registering a new category** = for every CMSG owning `a046_<anim>`, append a clone with `animationName`
  `a974_<anim>`, a fresh internal id (past the max) and a deep-copied binding set, as a new `generators` array item
  (pointer arrays are u64 item indices with PTCH entries of the element pointer type; the array field in DATA is
  item index + two zero u32s, count lives in ITEM). `er_behavior.register_categories(graph, 46, [974..989], slots)`
  does this: 23 crossbow slots × 16 categories → 928 clip generators in 78 selectors, TYPE section untouched,
  `validate()` checks every patched pointer. HkbEditor's "duplicate clip category" workflow and ERClipGenerator do the
  same (HkbEditor also appends to `animationNames`; not needed, FromSoft's own DLC export does not).
* Tagfile facts: ITEM = (u32 type index | flags << 24 [0x10 object, 0x20 array], u32 DATA offset, u32 count);
  PTCH = (u32 pointer type index, u32 count, u32 sorted offsets[count])* — groups per pointer type (`hkStringPtr`,
  `T*<hkbGenerator>`, `hkRefPtr<…>`, `hkArray<…>`); strings are `char` arrays (NUL counted) shared freely; objects
  16-aligned, strings 2-aligned; items in DATA-offset order.

### ER crossbow slots (TAE a46) — names from Smithbox's `Behavior-c0000-c0000.json` aliases
| IDs | State |
|---|---|
| 310x0 / 330x0 (x = 3 N, 4 F, 5 D) | **jump attack** neutral / forward / dash (R1 while airborne), bullet at 0.433 s; 330x1 = left-hand 2H |
| 31060 / 33060 | jump-loop attack (long fall) — melee swings |
| 31070 / 33070 (33073 left) | jump **land** attack (fire on landing); 31071/72, 31081/82 landing ends (idle / high / low) |
| 37000 / 37100 / 37300 | `AttackCrossbow{Right,BothRight,Left}Fire` — bullet at frame 0, `UnequipCrossbowBolt` |
| 37010 / 37110 / 37310 | `…Start` (raise ~0.5 s) → Loop if held, else Fire |
| 37020 / 37120 / 37320 | `…Loop Upper` (aim hold); 37025/37125/37325 `…Move Loop Upper` (walking) |
| 37030 / 37130 / 37330 | `…Reload` (`EquipCrossbowBolt` at 0.5 s → `GetBoltLoadingState`) |
| 37040 / 37140 / 37340 | `…Empty` (`IsOutOfAmmo`) |
| 37101 / 37131 | left-hand two-handing Fire / Reload |
| 375xx / 376xx / 378xx | stealth (crouch) versions of 370xx / 371xx / 373xx |
| 39700–39740 (39701/39731 variants) | mounted Fire / Start / Start-continue / Loop / Reload; 38100–38200 ride R2 / ride jump |
HKS flow (`AttackCrossbowRightStart_Upper_onUpdate` etc.): Start plays to `IsAnimEnd` (or `EventEzStateFlag 0`,
TAE event 227) → `ActionDuration(R1) > 0` ? Loop : Fire; Loop → Fire the frame R1 is released; R1 with the bolt
unloaded → Reload, with no ammo → Empty; R2 = same path with ammo slot 2 (`g_ArrowSlot`). Jump attacks need
`GetBoltLoadingState` true. **Hold-to-fire trick**: put the firing clip (with its `BulletBehavior`s) in the Loop
slots — events re-fire every loop cycle — and leave `UnequipCrossbowBolt` out of Fire so the bolt state stays loaded.
Bullets: `BulletBehavior` judge → `BehaviorParam_PC 1_VVVVV_JJJ` with VVVVV = the **ammo's** `behaviorVariationId`
(Bolt 5200 → 105200300 → Bullet 20007000); vanilla judges 300 (fire), 310/320–324/330/340–342 (bows, spread),
500/505 (Repeating Fire art, a935 40060: 12 bullets from one unequip — bullets do not need the loaded state).
`DummyPolyID` 55 = the crossbow model's bolt dummy (ballista 177); FFX dummies `10000 + N` = weapon dummy N.
ChrActionFlag ids: 4 cancel RH, 11 cancel movement, 16/22 cancel LH/guard, 25/30 input dodge/goods, 26/29/31/32
cancel dodge/magic/goods/jump, 87 input common, 90/91 walk/dash speed limit, 103–108 WA/quick-goods, 134 falling.
SpEffect 100130 = `[HKS] Stance - SetMoveType 1` (aim-walk). Havok variable 9 = 1 in the 2H family, var 0 ramps
0→1 at the end of full-body clips (`SetTimeActEditorHavokVariable`, 605).

### Binders — `tools/bnd4.py` (byte-identical on c0000.anibnd and regulation)
BND4 (`07D7R6`): 64-byte header (count @0xC, header size 0x40, entry size, data offset, unicode, raw format
byte — bit-reversed when byte 0x0A is set — extended 4 = path hash table, hash table offset @0x38); entries
(flags 0x40, i32 -1, q compressed size, q uncompressed size [Compression flag], I/q data offset [LongOffsets], i32 id,
I name offset); UTF-16 names; hash table (q hashes offset, i32 bucket count = first prime ≥ count/7, 0x00080810,
buckets (count, first index), entries (**hash u32, index i32**) grouped by `hash % buckets`, hash = Σ c·37 over the
lower-cased `/`-separated path with a leading `/`); file data in entry order, 16-aligned (empty files not padded).
Player anibnd ids: TAE = 5000000 + category, Skeleton 4000000, `c0000_a?x.txt` div placeholders 6000000+;
animation HKX in the `c0000_a?x.anibnd.dcx` divs with id = category·1,000,000 + animation (DS1: category·10,000 + id,
and the DS1 engine finds HKX in any loaded div). DCX/DFLT (zlib level 9, header 0x4C) is accepted by Elden Ring.
ER `.param` files carry **no ParamType string**; `er_param.py` maps file names to Paramdex defs (`DEF_ALIASES`).

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

### ER player animation files — from a vanilla `c0000_a5x.anibnd.dcx` (1282 HKX)
* Divs: `c0000_a?x.anibnd.dcx` (BND4 `07D7R6`, format 0x2E, entry flags 0x40, KRAK). Each holds whole TAE
  categories, not a numeric range: a5x = a050, a051, a052, **a972, a973** (+ empty `div_data.txt`, `ver_0001.txt`
  and `hkx\a5x_compendium\a5x.compendium`). Entry id = **1000000000 + cat·1000000 + anim** (`a050_030000.hkx` →
  1050030000); entry path `N:\GR\data\INTERROOT_win64\chr\c0000\hkx\a5x_compendium\aNNN_XXXXXX.hkx`. The
  binder rewrites byte-identically with `bnd4.py`; the game loads the div re-saved as DCX DFLT.
* Two tagfile flavours coexist in one div: **TCRF** files (no TYPE section; types come from the div's
  `.compendium`, a `TCM0` file with `TCID` + a TYPE section) and self-contained **TYPE** files. Every self-contained
  file uses the 2018 section magics **`TNA1`/`TBDY`** (same encoding as `TNAM`/`TBOD`, which is all Soulstruct
  writes). Root variants are always two: `"Scene Data"` (`hkxScene`, 3ds Max export) or `"Resource Data"` (empty
  `hkMemoryResourceContainer`), then `"Animation Container"`.
* Clips: `hkaSplineCompressedAnimation`, 30 fps (`frameDuration` 1/30), 150 transform tracks = all bones,
  binding `originalSkeletonName "Master"`, `transformTrackToBoneIndices` 0..149, 150 annotation tracks (bone names
  in the Scene-Data flavour, empty in the Resource-Data one), **always** an `hkaDefaultAnimatedReferenceFrame`
  (up +Y, forward +Z, one sample per frame, zeros when the clip stays in place), `maxFramesPerBlock` 256,
  `blockDuration` 8.5, **`maskAndQuantizationSize` = 4 × tracks = 600** (bytes of per-track mask/quantization
  headers at the start of each block — the track data starts right after them, so a wrong value scrambles the
  clip; Soulstruct's DS1 default 336 is wrong for ER), track header quantization byte 0x45.
* `tools/er_hkx.py`: tagfile section walker and **TYPE-section transplant** — a Soulstruct-written clip gets the
  TYPE section of a vanilla clip verbatim (TNA1/TBDY, `unsigned short` base type, all type-body flags, vanilla
  THSH hashes), with ITEM/PTCH type indices remapped by structural identity (name + templates, recursive). The
  result is indistinguishable from a vanilla "Resource Data" clip except for its data.
* soulstruct-havok patch additions: hk2018 `hkxScene`/`hkxNode`/`hkxCamera`/… and
  `hkMemoryResourceContainer`/`hkResource*` type modules (generated from the vanilla files), `Member.py_name`
  for keyword members (`from`), `TypeNotDefinedError` triggering type-module generation, and
  `BaseWrappedHKX.get_variant` finding the animation container when it is not variant 0 — so every vanilla ER
  player clip now loads (`HKX.from_bytes`).

## 5b. Elden Ring parts models (weapons) — from vanilla `wp_a_1503` / `wp_a_1513`
* Binder: BND4 `07D7R6` (format 0x74, hash table), entries 100 `{model}.tpf`, 200 `{model}.flver`, 400 `{model}.anibnd` and 800 `{model}_collidable.hkx` (rigged crossbows only), DCX KRAK. The `_l` binder (left hand) holds the **identical FLVER** named `{model}_L.flver` and a lower-resolution TPF whose textures are named `{stem}_l`.
* FLVER 0x2001A: header unk `x4a=0 x4c=0 x5c=0 x5d=2 x68=4`. Bone usage flags: 8 = used by mesh (`Body`), 4 = the mesh's default bone (`Model_…` / `cXXXX_…`), 2 = dummy parent (`Dmypoly_SFX`), 1 = unused rig bones. The post-texture `SekiroUnkStruct` (read/written by our Soulstruct patch) is a bone hierarchy table: per bone `(parent, child, next sibling, prev sibling), index`, listed twice (second list = the control rig order). Mesh: dynamic, **no local bone list** (vertex bone indices are global), layout Position/Normal(FourBytesB)/Tangent(FourBytesB)/BoneIndices/BoneWeights(FourBytesC)/Color(1)/UVPair, face sets flags 0/1/2 (LODs) + 128/129/130, `unk_x06 = 8`, backface culling on, `normal_w = 0`. The mesh "bounding box" floats are not the vertex min/max (unknown transform); the header bbox is.
* Material: name `0N - Default`, `mat_def_path = N:\GR\data\Material\mtd\Parts\matxml\{matbin}.matxml`, flags **1332** for `C[DetailBlend]`, **480** for `C[AMSN]_Glow`, one `GX00`/102 item (40 bytes: 12 zero, `ffffffff`, zeros) + terminator; every matbin sampler listed with an **empty path** (texture names come from the matbin; a path in the FLVER overrides it).
* Dummies: parent `Dmypoly_SFX`, attach −1, `follows_attach_bone 0`, `use_upward_vector 1`, `unk_x30 1`, `unk_x34 290619284`. The crossbow's only dummy (181) is where the loaded-bolt FFX (`SpawnFFX_Body_ForEventDuration 400400 @ 10181`) sits; bullets fire from **body** dummy 55 (`BulletBehavior DummyPolyID 55 AttachmentType 2`; left hand 56, bows 58, ballista 177). TAE events address weapon dummies as `10000+N` (right) / `20000+N` (left).
* TPF: PC, encoding 1, flags 3; `_a`/`_em` BC1_UNORM_SRGB with DX10 header (format code 0), `_m` BC4 (`ATI1`, 103), `_n` BC7_UNORM DX10 (106/107). ER normal maps: R/G normal, B gloss, A specular.
* **Weapon mount convention** (DS1 and, by the crossbow's geometry, ER): a weapon FLVER is mounted on `R_Weapon` **turned 180° about Z** — model (x, y, z) → bone (−x, −y, z). Evidence: a DS1 mod's muzzle dummies on `c0000.flver` (attached to `R_Weapon`) sit at bone-frame (+0.65, +0.13, 0) for a rifle whose mesh muzzle is at (−0.63, −0.12, 0), and ER's crossbow (mesh −Y forward, +X up) is held thumb-forward with the back of the hand up. In the bone frame +X is the finger direction, +Y the thumb side and +Z the palm (DS1 and ER rest poses agree; `R_Hand → R_Weapon` is a pure translation). So a pistol-grip weapon mesh points **−X** (receiver on −Y) and a thumb-forward (rifle/crossbow) grip points **−Y**; ER's crossbow convention is −Y forward, +X up. A ported weapon must adopt the target game's convention (rotate the mesh and rotate the `R_Weapon` track by the inverse, conjugated through the mount) or it looks wrong in every animation it does not override.
* `tools/er_model.py` builds parts binders with all of the above (`ERProfile` defaults = these values; `ERProfile.from_references` re-learns them).

## 6. Environment notes
* Soulstruct needs Python ≥ 3.13; its optional deps (`rich`, `typer`, `zstandard`, `frozendict`) are shimmed in `vendor/shims/`. `vendor/patches/soulstruct.patch` adds the Sekiro/Elden Ring FLVER unknown struct (`SekiroUnkStruct`: after the texture headers, `hh II 20x` counts/offsets then 16-byte members) that upstream neither reads nor writes; ER FLVERs (0x2001A) need it.
* Textures: Pillow ≥ 11 decodes DXT1/3/5, BC4/5/7 DDS; `tools/dds_tools.py` encodes BC1/BC3/BC4/BC5/BC7 with mip chains and DX10/sRGB headers through the vendored `bc7enc_rdo` (`vendor/setup.sh` builds it; Pillow encodes BC1/BC3/BC5 as a fallback). ER modders use BC7 sRGB for `_a`/`_m`, BC7 linear for `_n`; DXT1/DXT5 load too.
* Elden Ring texture conventions (from modding guides): `_a` albedo (sRGB); `_m` metallic (grey); `_n` packed — R/G = normal XY (G inverted vs OpenGL), **B = gloss**, **A = specular**; `_em` emissive. Weapon matbins live in `material/allmaterial.matbinbnd.dcx` (`Parts\matxml\P[WP_A_XXXX]_*.matbin`), almost all `C[DetailBlend]` (albedo/normal/metallic samplers `C_DetailBlend__snp_Texture2D_{7,0,3}_*`), glow ones `C[AMSN]_Glow[Int]` (+ `_1_EmissiveMap`).
* ER regulation ZSTD is decompressed (`zlib.zstdDecompressSync`) and compressed (`zlib.zstdCompressSync`) through Node ≥ 22.15; AES via Python `cryptography`.
* Oodle (DCX KRAK, Elden Ring's default for chr/parts/material files) is read with a Linux build of powzix's **ooz** (`vendor/setup.sh` clones it at `0503806`, applies `vendor/patches/ooz-linux.patch`, builds `vendor/ooz/oozdec`); `tools/dcx.py` drives it. The patch is a `stdafx.h` shim (MSVC intrinsics → GCC builtins; `_BitScanReverse`/`_byteswap_ulong` must stay 32-bit) plus one decoder fix: FromSoftware compresses with seek-chunk resets (block header byte `0x8C` = restart_decoder), so every 256 KB quantum is independently decodable and its own start is the LZ window base (first 8 bytes raw). Upstream ooz passes the buffer start as window for every quantum and fails on the second one. Decompression only: KRAK sources are written back as DFLT (`DCX_DFLT_10000_44_9`), which the game loads fine.
* DCX kinds seen: `DFLT` (c0000.anibnd, DS3-era files), `KRAK` (ER matbinbnd, partsbnd, most chr binders), `ZSTD` (ER ≥ 1.12 regulation). All go through `dcx.decompress()`.
