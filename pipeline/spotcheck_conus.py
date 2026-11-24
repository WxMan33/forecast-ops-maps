#!/usr/bin/env python3
"""Source-grid spot check for published CONUS tiles: for each field and checkpoint, the value decoded from the published
L0 tile must equal the value read straight from the GRIB message (same cell, same conversion), and the coarse levels must
be the documented corner cell or block maximum, rounded to the display step.
Usage: python pipeline/spotcheck_conus.py SITE_DIR RUN_ID|auto FH [--model hrrr|nbm|rrfs|ecmwf] [--only F,..] [--skip F,..] [--json out.json]
On a lat-lon model (ECMWF) the GRIB message is cropped to the published cells first, and its own grid keys must give the
manifest's grid.
ECMWF's Kuchera snowfall (asnowk_run) is recomputed here independently of the pipeline's reader: every step's sf, 2t, sp
and pressure-level t messages are fetched by their .index lines, decoded with eccodes directly, cropped by the manifest
grid, and the ratio, interval sums and run total are worked out again; the published values must match to one stored
unit (0.01 in, the encoding's quantisation)."""
import argparse, json, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hrrr, conus, fields as FX  # noqa: E402

POINTS = conus.CHECKPOINTS + [("Minneapolis (MSP)", 44.8848, -93.2223), ("Buffalo (BUF)", 42.9405, -78.7322), ("Salt Lake City (SLC)", 40.7884, -111.9778)]


def cell(grid, lat, lon):
    fi, fj = conus.to_cell(grid, lat, lon)
    return int(round(float(fj))), int(round(float(fi)))


def tile_value(site, rel, man, field, fh, level, j, i):
    rec = man["fields"][field]["hours"].get(str(fh))
    if not rec or "notApplicable" in rec: return None
    s = 2 ** level; jj, ii = j // s, i // s; ty, tx = jj // conus.TILE, ii // conus.TILE
    name = f"L{level}_{tx}_{ty}.bin.gz"; lv = next(x for x in man["levels"] if x["level"] == level)
    ny = min(conus.TILE, lv["ny"] - ty * conus.TILE); nx = min(conus.TILE, lv["nx"] - tx * conus.TILE)
    if "pack" in rec:   # packed layout (R2): tiles are gzip members in PACK order (levels coarsest first, rows south to north)
        names = [n for L in sorted(conus.LEVELS, reverse=True) for n, _ in conus.tiles_for(np.zeros((man["grid"]["ny"], man["grid"]["nx"]), np.int16), L, "corner cell", 1)]
        k = names.index(name); lens = rec["pack"]["lengths"]; off = sum(lens[:k])
        with open(os.path.join(site, rel, rec["pack"]["file"]), "rb") as fp: fp.seek(off); a = conus.decode_tile(fp.read(lens[k]), ny, nx)
    else:
        with open(os.path.join(site, rel, field, f"f{fh:02d}", name), "rb") as fp: a = conus.decode_tile(fp.read(), ny, nx)
    return int(a[jj - ty * conus.TILE, ii - tx * conus.TILE])


def kuchera_independent(cyc, fh, grid):
    """Cropped Kuchera run total (inches) at step fh straight from the GRIB messages, without the pipeline's readers."""
    import concurrent.futures as cf
    from eccodes import codes_new_from_message, codes_get, codes_get_values, codes_release
    import ecmwf
    rows_g = grid["j0"] + grid["rowStep"] * np.arange(grid["ny"]); cols = np.arange(grid["i0"], grid["i0"] + grid["nx"])
    def msg(url, rows, param, lev, s, short, tol, level):
        r = [x for x in rows if x["var"] == param and x["level"] == lev and x["step"] == str(s)]
        if len(r) != 1: raise RuntimeError(f"{param} {lev} step {s}: {len(r)} index lines")
        g = codes_new_from_message(ecmwf.http(url, (r[0]["start"], r[0]["end"])))
        try:
            ok = (codes_get(g, "shortName") == short and codes_get(g, "typeOfLevel") == tol and (level is None or codes_get(g, "level") == level)
                  and codes_get(g, "endStep") == s and codes_get(g, "Ni") == grid["Ni"] and codes_get(g, "Nj") == grid["Nj"]
                  and codes_get(g, "latitudeOfFirstGridPointInDegrees") == grid["latitudeOfFirstGridPointInDegrees"]
                  and codes_get(g, "dataDate") == int(f"{cyc:%Y%m%d}") and codes_get(g, "dataTime") == cyc.hour * 100)
            if short == "sf": ok = ok and codes_get(g, "startStep") == 0
            if not ok: raise RuntimeError(f"{param} {lev} step {s}: GRIB keys do not match")
            return codes_get_values(g).reshape(grid["Nj"], grid["Ni"])[rows_g][:, cols].astype(np.float64)
        finally: codes_release(g)
    def step(s):
        url = ecmwf.url_of(cyc, s); rows = ecmwf.index_rows(url)
        sf = msg(url, rows, "sf", "sfc", s, "sf", "surface", None); t2 = msg(url, rows, "2t", "sfc", s, "2t", "heightAboveGround", 2)
        sp = msg(url, rows, "sp", "sfc", s, "sp", "surface", None)
        prof = [t2] + [np.where(p * 100.0 <= sp, msg(url, rows, "t", f"pl:{p}", s, "t", "isobaricInhPa", p), -np.inf) for p in (1000, 925, 850, 700, 600, 500)]
        tmax = np.max(np.stack(prof), axis=0)
        ratio = np.clip(np.where(tmax <= 271.16, 12 + (271.16 - tmax), 12 + 2 * (271.16 - tmax)), 0, None)
        return sf, ratio
    steps = list(range(3, fh + 1, 3))
    with cf.ThreadPoolExecutor(3) as ex: got = list(ex.map(step, steps))
    total = np.zeros((grid["ny"], grid["nx"])); before = np.zeros_like(total)
    for sf, ratio in got:
        total = total + np.maximum(sf - before, 0.0) * (1000 / 25.4) * ratio; before = sf
    return total


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("site"); ap.add_argument("run"); ap.add_argument("fh", type=int); ap.add_argument("--json"); ap.add_argument("--model", default="hrrr", choices=("hrrr", "nbm", "rrfs", "ecmwf"))
    ap.add_argument("--only", help="check only these fields (comma-separated)"); ap.add_argument("--skip", help="leave these fields out (comma-separated), e.g. a costly recomputation at a late step")
    a = ap.parse_args()
    if a.run == "auto":   # the newest run in the built site that has this hour
        idx = json.load(open(os.path.join(a.site, "data", "conus", *([a.model] if a.model != "hrrr" else []), "index.json")))
        pick = [r for r in idx["runs"] if a.fh in r["hours"]]
        if not pick: print(f"no run with f{a.fh:02d} to check"); return
        a.run = pick[0]["id"]; print(f"checking run {a.run} f{a.fh:02d}")
    rel = next((r["path"] for r in json.load(open(os.path.join(a.site, "data", "conus", *([a.model] if a.model != "hrrr" else []), "index.json")))["runs"] if r["id"] == a.run), f"data/conus/{a.model}/{a.run}/{conus.PRODUCT}")   # tiles or packed
    man = json.load(open(os.path.join(a.site, rel, "manifest.json")))
    grid = {k: v for k, v in man["grid"].items() if k != "projection"}
    import datetime as dt
    cyc = dt.datetime.strptime(a.run, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    if a.model == "nbm":
        import nbm; nbm.use_nbm(cyc)
    if a.model == "rrfs":
        import rrfs; rrfs.use_rrfs(cyc, man["hoursExpected"])
    if a.model == "ecmwf":
        import ecmwf; ecmwf.use_ecmwf(cyc, man["hoursExpected"])
    rows_of = conus.run_rows(cyc); TABLE = conus.FIELDS
    out, bad = [], 0
    only = set(a.only.split(",")) if a.only else None; skip = set(a.skip.split(",")) if a.skip else set()
    for f, spec in TABLE.items():
        if (only and f not in only) or f in skip: out.append({"field": f, "status": "not checked in this call (--only/--skip)"}); continue
        rec = man["fields"][f]["hours"].get(str(a.fh))
        if rec is None: out.append({"field": f, "status": "missing hour"}); continue
        if "notApplicable" in rec: out.append({"field": f, "status": "not applicable", "why": rec["notApplicable"]}); continue
        tol = 0
        if f == "asnowk_run":   # independent recomputation from the GRIB (not the pipeline's reader); one stored unit of tolerance
            v = kuchera_independent(cyc, a.fh, grid); tol = 1; g = None
        else:
            k, v, src = spec["read"](rows_of(a.fh)[0], rows_of(a.fh)[1], a.fh, rows_of)
            g = conus.grid_of(k)
        if g is not None and conus.is_ll(g):   # a crop of a global lat-lon message: the message's own keys must give the published grid
            if g != grid: out.append({"field": f, "point": "grid", "match": False, "grib": None, "shown": None, "units": "", "L1": None, "L2": None}); bad += 1; continue
            v = conus.crop(v, g)
        q = FX.encode(f, v, TABLE)
        if "points" in rec:   # every label value of the Values overlay equals the GRIB cell it names
            import gzip as _gz
            ij, _d = conus.label_points(grid)
            got = np.frombuffer(_gz.decompress(conus.points_bytes(os.path.join(a.site, rel), f, a.fh, rec)), "<i2")
            want = np.array([q[j, i] for i, j in ij], np.int16)
            nbad = int((np.abs(got.astype(int) - want.astype(int)) > tol).sum()) if got.size == want.size else -1
            out.append({"field": f, "point": f"label points ({got.size}: {len(_d['places'])} places + {_d['lattice']['nx']}x{_d['lattice']['ny']} grid)", "match": nbad == 0, "grib": None, "shown": None, "units": "", "L1": None, "L2": None, "labelPointsWrong": nbad,
                        **({"labelPointsNotExact": int((got != want).sum())} if tol and got.size == want.size else {})}); bad += nbad != 0
        pts = [(name, *cell(grid, lat, lon)) for name, lat, lon in POINTS]
        fin = np.isfinite(v) & (np.nan_to_num(v) > 0)
        if fin.any():   # the field's own maximum and three random non-zero cells, so wet and snowy cells are checked too
            jm, im = np.unravel_index(np.nanargmax(np.where(fin, v, -np.inf)), v.shape); pts.append(("field maximum", int(jm), int(im)))
            js, is_ = np.nonzero(fin); rng = np.random.default_rng(7)
            for n in rng.choice(len(js), size=min(3, len(js)), replace=False): pts.append(("random non-zero cell", int(js[n]), int(is_[n])))
        for name, j, i in pts:
            want0 = int(q[j, i]); got0 = tile_value(a.site, rel, man, f, a.fh, 0, j, i)
            row = {"field": f, "point": name, "cell": [j, i], "grib": None if want0 == hrrr.MISS16 else round(float(v[j, i]), 4),
                   "stored": got0, "shown": None if got0 in (None, hrrr.MISS16) else round(got0 * spec["scale"], 4), "units": spec["units"]}
            ok = got0 is not None and abs(got0 - want0) <= tol and (got0 == hrrr.MISS16) == (want0 == hrrr.MISS16)
            for L in (1, 2):   # coarse levels: the documented rule applied to the stored native values, in display steps
                s = 2 ** L; jb, ib = (j // s) * s, (i // s) * s
                block = q[jb:jb + s, ib:ib + s]
                ref = (block.max() if spec["coarse"] == "block max" else q[jb, ib])
                if ref != hrrr.MISS16 and spec["displayStep"] > 1: ref = int(np.clip(np.round(ref / spec["displayStep"]), -32767, 32767))
                got = tile_value(a.site, rel, man, f, a.fh, L, j, i); row[f"L{L}"] = got; ok = ok and got is not None and abs(got - int(ref)) <= tol
            row["match"] = bool(ok); bad += not ok; out.append(row)
    for r in out:
        if "point" in r: print(f'{"ok  " if r["match"] else "BAD "} {r["field"]:9} {r["point"]:28} GRIB {r["grib"]} -> shown {r["shown"]} {r["units"]} (L1 {r["L1"]}, L2 {r["L2"]})'
                               + (f' [{r["labelPointsNotExact"]} off by one stored unit]' if "labelPointsNotExact" in r else ""))
        else: print(f'--   {r["field"]:9} {r["status"]}{": " + r["why"] if r.get("why") else ""}')
    if a.json: json.dump({"run": a.run, "fh": a.fh, "points": out}, open(a.json, "w"), indent=1)
    print(f"{'all' if not bad else bad} {'cells match the source grid' if not bad else 'MISMATCHES'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
