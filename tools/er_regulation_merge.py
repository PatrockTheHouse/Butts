"""Three-way merge of Elden Ring regulation.bin files, row by row (field by field on conflicts).

    er_regulation_merge.py BASE.bin OURS.bin THEIRS.bin OUT.bin [--prefer ours|theirs] [--only Param,Param] [--report REPORT.txt]

BASE is the regulation both mods were built from (the game's own regulation.bin), OURS the one whose changes must
all survive (it also decides the DCX kind of the output), THEIRS the other mod's. For every PARAM in the three
files, each row id is classified by comparing its bytes with the base:

    theirs == base                      -> ours (unchanged by them)
    theirs != base, ours == base        -> theirs (added, changed or deleted by them)
    both changed, different fields      -> ours with their changed fields patched in (Paramdex defs)
    both changed, the same field        -> --prefer (default ours); listed in the report
    deleted by one, changed by other    -> the change wins; listed in the report

Params that exist only in one side are copied as they are; rows keep their name offsets. The result is written
with the same DCX kind as OURS (ZSTD for 1.12+, DFLT / KRAK before) and re-encrypted. Library use:
`merge_regulation(base_bytes, ours_bytes, theirs_bytes, prefer="ours") -> (out_bytes, report_lines)`.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import modkit_env  # noqa: E402,F401
import dcx  # noqa: E402
from bnd4 import BND4, BinderFile  # noqa: E402
from er_param import ERParam, decode_row, encode_row, rebuild_param, validate_param  # noqa: E402
from er_regulation import decrypt_regulation, write_regulation  # noqa: E402


def param_rows(buf: bytes) -> tuple[dict[int, bytes], int]:
    """{row id: row bytes} of a 64-bit PARAM and its row size (the layout rebuild_param writes)."""
    strings_off = struct.unpack_from("<I", buf, 0)[0]
    count = struct.unpack_from("<H", buf, 0x0A)[0]
    flags2d = buf[0x2D]
    if not (flags2d & 0x04):
        raise NotImplementedError("only 64-bit (LongDataOffset) PARAMs are supported")
    row_table = 0x40 if flags2d & 0x05 else 0x30
    entries = [struct.unpack_from("<iiQQ", buf, row_table + 24 * i) for i in range(count)]
    if count > 1:
        row_size = entries[1][2] - entries[0][2]
    elif count == 1:
        row_size = strings_off - entries[0][2]
    else:
        row_size = 0
    return {rid: buf[doff: doff + row_size] for rid, _pad, doff, _noff in entries}, row_size


def _open(regulation: bytes) -> tuple[BND4, bytes]:
    dec = decrypt_regulation(regulation)
    return BND4.read(dcx.decompress(dec)), dcx.kind(dec)


def _param_name(entry_name: str) -> str:
    return entry_name.split("\\")[-1].removesuffix(".param")


def merge_params(name: str, base: bytes | None, ours: bytes | None, theirs: bytes | None, prefer: str,
                 report: list[str]) -> bytes | None:
    """Merge one PARAM; returns the merged bytes (or the surviving side's when a side lacks the param)."""
    if ours is None and theirs is None:
        return base
    if base is None:
        if ours is not None and theirs is not None and ours != theirs:
            report.append(f"{name}: new in both mods, differs; keeping {prefer}")
            return ours if prefer == "ours" else theirs
        return ours if ours is not None else theirs
    if ours is None:
        report.append(f"{name}: missing from ours, taking theirs")
        return theirs
    if theirs is None:
        report.append(f"{name}: missing from theirs, keeping ours")
        return ours
    if theirs == base:
        return ours
    if ours == base:
        rb, _ = param_rows(base)
        rt, _ = param_rows(theirs)
        changed = sum(1 for rid in set(rb) | set(rt) if rb.get(rid) != rt.get(rid))
        report.append(f"{name}: taken from theirs ({changed} rows differ from base, none of ours)")
        return theirs
    rb, rs_b = param_rows(base)
    ro, rs_o = param_rows(ours)
    rt, rs_t = param_rows(theirs)
    if not (rs_b == rs_o == rs_t):
        report.append(f"{name}: row size differs ({rs_b}/{rs_o}/{rs_t}): different game versions; keeping {prefer}")
        return ours if prefer == "ours" else theirs
    fields = None
    new_rows: dict[int, bytes] = {}
    remove: list[int] = []
    n_theirs = n_fields = n_conflict = 0
    for rid in sorted(set(rb) | set(ro) | set(rt)):
        b, o, t = rb.get(rid), ro.get(rid), rt.get(rid)
        if t == b:                       # untouched by them
            continue
        if o == b:                       # only they touched it
            if t is None:
                remove.append(rid)
            else:
                new_rows[rid] = t
            n_theirs += 1
            continue
        if o == t:                       # both made the same change
            continue
        # both changed it
        if t is None:
            report.append(f"{name} {rid}: deleted by theirs, changed by ours; keeping ours")
            continue
        if o is None:
            report.append(f"{name} {rid}: deleted by ours, changed by theirs; keeping theirs")
            new_rows[rid] = t
            continue
        if b is None:                    # both added the same id with different content
            report.append(f"{name} {rid}: added by both with different values; keeping {prefer}")
            if prefer == "theirs":
                new_rows[rid] = t
            n_conflict += 1
            continue
        if fields is None:
            fields = ERParam(base, name).fields
        vb, _ = decode_row(b, fields)
        vo, _ = decode_row(o, fields)
        vt, _ = decode_row(t, fields)
        ours_changed = {k for k in vb if vo.get(k) != vb[k]}
        theirs_changed = {k for k in vb if vt.get(k) != vb[k]}
        clash = ours_changed & theirs_changed
        take = {k: vt[k] for k in theirs_changed - clash}
        if clash:
            n_conflict += 1
            report.append(f"{name} {rid}: both changed {sorted(clash)}; keeping {prefer} there")
            if prefer == "theirs":
                take.update({k: vt[k] for k in clash})
        if take:
            merged = encode_row(o, fields, {k: v for k, v in take.items() if not isinstance(v, (list, tuple))})
            new_rows[rid] = merged
            n_fields += 1
    if not new_rows and not remove:
        return ours
    out = rebuild_param(ours, new_rows, remove=remove)
    validate_param(out)
    report.append(f"{name}: {n_theirs} rows from theirs, {n_fields} rows field-merged, {n_conflict} conflicts"
                  + (f", {len(remove)} rows deleted" if remove else ""))
    return out


def merge_regulation(base: bytes, ours: bytes, theirs: bytes, prefer: str = "ours", only: set[str] | None = None) -> tuple[bytes, list[str]]:
    bb, _ = _open(base)
    bo, kind = _open(ours)
    bt, _ = _open(theirs)
    by_name = lambda bnd: {_param_name(f.name): f for f in bnd.files}  # noqa: E731
    pb, po, pt = by_name(bb), by_name(bo), by_name(bt)
    report: list[str] = []
    for name in sorted(set(pb) | set(po) | set(pt)):
        if only and name not in only:
            continue
        merged = merge_params(name, pb[name].data if name in pb else None, po[name].data if name in po else None,
                              pt[name].data if name in pt else None, prefer, report)
        if merged is None:
            continue
        if name in po:
            po[name].data = merged
        else:                            # a param ours lacks: append their entry
            src = pt[name] if name in pt else pb[name]
            bo.files.append(BinderFile(src.id, src.name, merged, src.flags, src.raw_pad))
            report.append(f"{name}: appended (not in ours)")
    out = write_regulation(bo.write(), kind)
    back, _ = _open(out)
    assert back.write() == bo.write(), "regulation read-back mismatch"
    return out, report


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts = sys.argv[1:]
    if len(args) != 4:
        print(__doc__)
        sys.exit(2)
    prefer = "theirs" if "--prefer" in opts and opts[opts.index("--prefer") + 1] == "theirs" else "ours"
    only = set(opts[opts.index("--only") + 1].split(",")) if "--only" in opts else None
    base, ours, theirs, out = (Path(a) for a in args)
    merged, report = merge_regulation(base.read_bytes(), ours.read_bytes(), theirs.read_bytes(), prefer, only)
    out.write_bytes(merged)
    text = "\n".join(report) if report else "no differences between theirs and base"
    print(text)
    if "--report" in opts:
        Path(opts[opts.index("--report") + 1]).write_text(text + "\n")
    print(f"{out}: {len(merged)} bytes")


if __name__ == "__main__":
    main()
