#!/usr/bin/env python3
"""Source-grid spot check for published CONUS tiles: for each field and checkpoint, the value decoded from the published
L0 tile must equal the value read straight from the GRIB message (same cell, same conversion), and the coarse levels must
be the documented corner cell or block maximum, rounded to the display step.
Usage: python pipeline/spotcheck_conus.py SITE_DIR RUN_ID|auto FH [--json out.json]"""
import argparse, json, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hrrr, conus, fields as FX  # noqa: E402

POINTS = conus.CHECKPOINTS + [("Minneapolis (MSP)", 44.8848, -93.2223), ("Buffalo (BUF)", 42.9405, -78.7322), ("Salt Lake City (SLC)", 40.7884, -111.9778)]


def cell(grid, lat, lon):
    fwd = hrrr.lcc(grid); x, y = fwd(lat, lon)
    return int(round((float(y) - grid["y0"]) / grid["DyInMetres"])), int(round((float(x) - grid["x0"]) / grid["DxInMetres"]))


def tile_value(site, rel, man, field, fh, level, j, i):
    rec = man["fields"][field]["hours"].get(str(fh))
    if not rec or "notApplicable" in rec: return None
    s = 2 ** level; jj, ii = j // s, i // s; ty, tx = jj // conus.TILE, ii // conus.TILE
    name = f"L{level}_{tx}_{ty}.bin.gz"; lv = next(x for x in man["levels"] if x["level"] == level)
    ny = min(conus.TILE, lv["ny"] - ty * conus.TILE); nx = min(conus.TILE, lv["nx"] - tx * conus.TILE)
    with open(os.path.join(site, rel, field, f"f{fh:02d}", name), "rb") as fp: a = conus.decode_tile(fp.read(), ny, nx)
    return int(a[jj - ty * conus.TILE, ii - tx * conus.TILE])


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("site"); ap.add_argument("run"); ap.add_argument("fh", type=int); ap.add_argument("--json")
    a = ap.parse_args()
    if a.run == "auto":   # the newest run in the built site that has this hour
        idx = json.load(open(os.path.join(a.site, "data", "conus", "index.json")))
        pick = [r for r in idx["runs"] if a.fh in r["hours"]]
        if not pick: print(f"no run with f{a.fh:02d} to check"); return
        a.run = pick[0]["id"]; print(f"checking run {a.run} f{a.fh:02d}")
    rel = f"data/conus/hrrr/{a.run}/{conus.PRODUCT}"
    man = json.load(open(os.path.join(a.site, rel, "manifest.json")))
    grid = {k: v for k, v in man["grid"].items() if k != "projection"}
    import datetime as dt
    cyc = dt.datetime.strptime(a.run, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc); rows_of = conus.run_rows(cyc)
    out, bad = [], 0
    for f, spec in FX.FIELDS.items():
        rec = man["fields"][f]["hours"].get(str(a.fh))
        if rec is None: out.append({"field": f, "status": "missing hour"}); continue
        if "notApplicable" in rec: out.append({"field": f, "status": "not applicable", "why": rec["notApplicable"]}); continue
        k, v, src = spec["read"](rows_of(a.fh)[0], rows_of(a.fh)[1], a.fh, rows_of)
        q = FX.encode(f, v)
        if "points" in rec:   # every label value of the Values overlay equals the GRIB cell it names
            import gzip as _gz
            ij, _d = conus.label_points(grid)
            got = np.frombuffer(_gz.decompress(open(os.path.join(a.site, rel, f, f"p{a.fh:02d}.bin.gz"), "rb").read()), "<i2")
            want = np.array([q[j, i] for i, j in ij], np.int16); nbad = int((got != want).sum()) if got.size == want.size else -1
            out.append({"field": f, "point": f"label points ({got.size}: {len(_d['places'])} places + {_d['lattice']['nx']}x{_d['lattice']['ny']} grid)", "match": nbad == 0, "grib": None, "shown": None, "units": "", "L1": None, "L2": None, "labelPointsWrong": nbad}); bad += nbad != 0
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
            ok = got0 == want0
            for L in (1, 2):   # coarse levels: the documented rule applied to the stored native values, in display steps
                s = 2 ** L; jb, ib = (j // s) * s, (i // s) * s
                block = q[jb:jb + s, ib:ib + s]
                ref = (block.max() if spec["coarse"] == "block max" else q[jb, ib])
                if ref != hrrr.MISS16 and spec["displayStep"] > 1: ref = int(np.clip(np.round(ref / spec["displayStep"]), -32767, 32767))
                got = tile_value(a.site, rel, man, f, a.fh, L, j, i); row[f"L{L}"] = got; ok = ok and got == int(ref)
            row["match"] = bool(ok); bad += not ok; out.append(row)
    for r in out:
        if "point" in r: print(f'{"ok  " if r["match"] else "BAD "} {r["field"]:9} {r["point"]:28} GRIB {r["grib"]} -> shown {r["shown"]} {r["units"]} (L1 {r["L1"]}, L2 {r["L2"]})')
        else: print(f'--   {r["field"]:9} {r["status"]}{": " + r["why"] if r.get("why") else ""}')
    if a.json: json.dump({"run": a.run, "fh": a.fh, "points": out}, open(a.json, "w"), indent=1)
    print(f"{'all' if not bad else bad} {'cells match the source grid' if not bad else 'MISMATCHES'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
