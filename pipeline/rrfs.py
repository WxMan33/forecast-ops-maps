#!/usr/bin/env python3
"""RRFS (Rapid Refresh Forecast System, NCEP operational) 3 km CONUS for Forecast Ops Model Maps, in the conus-v1 format
(pipeline/conus.py does the tiling, label points, carry-forward and manifests; this file says which RRFS messages make
each field and which runs to keep).

Source: NOAA RRFS on NOAA Open Data Dissemination (AWS), s3://noaa-rrfs-ops-pds/rrfs.YYYYMMDD/HH/
rrfs.tHHz.2dfld.3km.fFFF.conus.grib2 (+ .idx). Not the pre-operational bucket noaa-rrfs-pds. The 3 km CONUS file is
written by the 00/06/12/18Z cycles (to F84) and the 03/09/15/21Z cycles (to F18), checked 22 Nov 2026; other hourly
cycles do not write it. Its grid is the HRRR CONUS grid exactly (Lambert conformal, 1799 x 1059 at 3 km, LaD = Latin =
38.5N, LoV 262.5E; checked against the GRIB keys), so HRRR and RRFS cells, tiles and label points line up.

Each message is confirmed from its own GRIB keys, as for HRRR, with two differences found on the live files:
  composite reflectivity is WMO 0/16/5 on 'atmosphereSingleLayer' (HRRR: NCEP local 0/16/196, 'atmosphere');
  precipitation type is diagnosed at F00 too (HRRR's analysis sets no flags), so F00 type is published.
There is no freezing-rain accumulation (FRZR) in this file, so that field is not offered for RRFS.

Usage: python pipeline/rrfs.py --site SITE [--published URL] [--prev DIR] [--through 48] [--pack] [--carry-only]
"""
import argparse, datetime as dt, json, os, re, shutil, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hrrr, conus, fields as FX  # noqa: E402

BUCKET = "https://noaa-rrfs-ops-pds.s3.amazonaws.com"
LONG, SHORT = (0, 6, 12, 18), (3, 9, 15, 21)   # cycles that write the 3 km CONUS file: to F84 and to F18
THROUGH = 48          # long runs are published to F48 for now (R2 storage and loop length); short runs to F18
UTC = dt.timezone.utc
TERMS = ("NOAA/NCEP RRFS output is a U.S. Government work with no copyright restriction in the United States; NOAA Open Data "
         "Dissemination (AWS Open Data, Registry of Open Data 'noaa-rrfs', operational bucket noaa-rrfs-ops-pds) states no restrictions "
         "on use or redistribution. Attribute NOAA/NCEP; do not imply NOAA endorsement.")


def url_of(cycle, fh): return f"{BUCKET}/rrfs.{cycle:%Y%m%d}/{cycle:%H}/rrfs.t{cycle:%H}z.2dfld.3km.f{fh:03d}.conus.grib2"


def run_rows(cycle):
    cache = {}
    def rows_of(fh):
        if fh not in cache: u = url_of(cycle, fh); cache[fh] = (u, hrrr.idx_rows(u))
        return cache[fh]
    return rows_of


def read_refc(url, rows, fh, rows_of):
    # WMO 0/16/5 'composite reflectivity' (eccodes names it 'unknown'); identity by the number triple, level type and step
    k, v, s = FX._get(url, rows, "REFC", ["entire atmosphere (considered as a single layer)", "entire atmosphere"], f"{fh} hour fcst" if fh else "anl",
                      lambda k: FX._param(k, 0, 16, 5) and k["typeOfLevel"] == "atmosphereSingleLayer" and FX._instant(k, fh), fh)
    return k, v, [s]


def read_ptype(url, rows, fh, rows_of): return FX.ptype_flags(url, rows, fh)


def n_hours(cycle, through=None): return min(through or THROUGH, 84) if cycle.hour in LONG else 18


SRC = "NOAA RRFS rrfs.tHHz.2dfld.3km.fFFF.conus.grib2"


def spec_from_hrrr(f, **over):
    sp = {**FX.FIELDS[f], **over}
    sp["source"] = sp["source"].replace("NOAA HRRR wrfsfcfNN.grib2", SRC)
    sp["label"] = sp["label"].replace("(HRRR)", "(RRFS)")
    sp["derivation"] = sp["derivation"].replace("HRRR's", "RRFS's").replace("HRRR publishes", "RRFS publishes")
    if "note" in sp: sp["note"] = sp["note"].replace("HRRR", "RRFS")
    return {**sp, "attribution": "NOAA/NCEP RRFS", "terms": TERMS}


FIELDS = {
    "t2m": spec_from_hrrr("t2m"), "d2m": spec_from_hrrr("d2m"), "gust": spec_from_hrrr("gust"),
    "refc": spec_from_hrrr("refc", read=read_refc, source=f"{SRC}, REFC entire atmosphere (considered as a single layer) (GRIB 0/16/5 WMO composite reflectivity, atmosphereSingleLayer, instant, dB)"),
    "ptype": spec_from_hrrr("ptype", read=read_ptype, firstHour=0),
    "apcp1h": spec_from_hrrr("apcp1h"), "apcp_run": spec_from_hrrr("apcp_run"), "asnow1h": spec_from_hrrr("asnow1h"), "asnow_run": spec_from_hrrr("asnow_run"),
}


def complete_runs(now, look=30):
    """Cycles (newest first) with every hour of their published span posted, and whose last hour really carries the fields."""
    out = []
    for back in range(1, look + 1):
        c = (now - dt.timedelta(hours=back)).replace(minute=0, second=0, microsecond=0)
        if c.hour not in LONG + SHORT: continue
        n = n_hours(c); q = f"{BUCKET}/?list-type=2&prefix=rrfs.{c:%Y%m%d}/{c:%H}/rrfs.t{c:%H}z.2dfld.3km.f&max-keys=1000"
        try: x = hrrr.http(q).decode()
        except Exception: continue
        posted = {int(h): mod for h, mod in re.findall(r"<Key>[^<]*2dfld\.3km\.f(\d{3})\.conus\.grib2\.idx</Key><LastModified>([^<]+)</LastModified>", x)}
        if not all(h in posted for h in range(0, n + 1)): continue
        try: last = hrrr.idx_rows(url_of(c, n))
        except Exception: continue
        if not any(r["var"] == "TMP" and r["level"] == "2 m above ground" for r in last): continue
        out.append((c, n, {h: posted[h] for h in range(0, n + 1)}))
    return out


def keep(runs):
    """The two newest complete runs, plus the newest long run if neither is one (for planning and the comparison)."""
    k = runs[:2]
    if not any(c.hour in LONG for c, _, _ in k):
        lng = next((r for r in runs if r[0].hour in LONG), None)
        if lng: k.append(lng)
    return k


def use_rrfs(cycle, n):
    conus.FIELDS = FIELDS
    conus.MODEL = {"name": "RRFS", "slug": "rrfs", "firstHour": 0, "expected": list(range(0, n + 1)), "domain": "CONUS 3 km (full native RRFS grid, same as HRRR)",
                   "run_rows": run_rows, "attribution": "NOAA/NCEP RRFS via NOAA Open Data Dissemination (AWS). Rendered by Forecast Ops. Model guidance, not an official forecast."}


def main():
    global THROUGH
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True); ap.add_argument("--published", default=conus.SITE_URL); ap.add_argument("--now")
    ap.add_argument("--prev"); ap.add_argument("--through", type=int, default=48); ap.add_argument("--pack", action="store_true"); ap.add_argument("--changed-output")
    a = ap.parse_args()
    THROUGH = a.through
    if a.pack: conus.PACK, conus.PRODUCT = True, "conus-v1-packed"
    conus.PREV = a.prev if a.prev and os.path.isdir(a.prev) else None
    now = dt.datetime.fromisoformat(a.now).replace(tzinfo=UTC) if a.now else dt.datetime.now(UTC)
    root = os.path.join(a.site, "data", "conus", "rrfs")
    try: prev = json.loads(hrrr.http(f"{a.published}/data/conus/rrfs/index.json"))
    except Exception: prev = {"runs": []}
    have = {r["id"]: r for r in prev.get("runs", [])}
    t0 = time.time(); runs = keep(complete_runs(now))
    if not runs: raise SystemExit("no complete RRFS run posted")
    out, failures, processed = [], [], 0
    for c, n, posted in runs:
        use_rrfs(c, n)
        rid = c.strftime("%Y%m%d%H"); rel = f"data/conus/rrfs/{rid}/{conus.PRODUCT}"; dest = os.path.join(a.site, rel); old = None
        if rid in have:
            try: old = json.loads(hrrr.http(f"{a.published}/{rel}/manifest.json"))
            except Exception: old = None
        try:
            man, p = conus.build_run(c, "long" if c.hour in LONG else "short", n, True, posted, dest, rel, old, a.published); processed += p
            out.append(conus.summary(man, rel))
        except Exception as e:
            failures.append({"id": rid, "error": str(e)[:300]}); shutil.rmtree(dest, ignore_errors=True)
    if not out: raise SystemExit("nothing publishable: " + json.dumps(failures))
    index = {"schema": 1, "product": conus.PRODUCT, "model": "RRFS", "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "runs": out, "failures": failures,
             "retention": f"the two newest complete RRFS runs with a 3 km CONUS file (00/06/12/18Z to F{THROUGH:02d}, 03/09/15/21Z to F18), plus the newest 00/06/12/18Z run",
             "build": {"seconds": round(time.time() - t0, 1), "hoursProcessed": processed, "filesCarried": dict(conus.CARRY_STATS)}}
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "index.json"), "w") as fp: json.dump(index, fp, indent=1)
    if a.changed_output:
        with open(a.changed_output, "a") as fp: fp.write(f"rrfs_changed={'true' if processed or sorted(r['id'] for r in out) != sorted(have) else 'false'}\n")
    print(json.dumps({"failures": failures, "build": index["build"]}), [(r["id"], r["role"], r["status"], len(r["hours"])) for r in out])


if __name__ == "__main__":
    main()
