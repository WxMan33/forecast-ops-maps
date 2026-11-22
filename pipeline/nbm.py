#!/usr/bin/env python3
"""National Blend of Models (NBM v4.x core, CONUS 2.5 km) for Forecast Ops Model Maps, in the same conus-v1 tile format
as HRRR (pipeline/conus.py does the tiling, label points, carry-forward and manifests; this file only says which NBM
messages make each field and which runs to keep).

Source: NOAA NBM on NOAA Open Data Dissemination (AWS), s3://noaa-nbm-grib2-pds/blend.YYYYMMDD/HH/core/
blend.tHHz.core.fFFF.co.grib2 (+ .idx). Messages are chosen by their .idx line INCLUDING the qualifier after the step
(NBM posts a deterministic TMP and an "ens std dev" TMP under the same name/level/step), then confirmed from GRIB:
  TMP 2 m   0/0/0  template 4.0 (deterministic), instant, K
  PoP       APCP 0/1/8 template 4.9 (probability), probabilityType 1 (above the upper limit), upper limit 0.254 kg m-2
            (= 0.01 in), accum over the stated window; values are percent 0-100.
1-h PoP windows end every hour; 6-h PoP windows end at 00/06/12/18Z valid time (verified 22 Nov 2026 12Z and 13Z runs).

Usage: python pipeline/nbm.py --site SITE [--published URL] [--prev DIR] [--max-hours N] [--carry-only]
"""
import argparse, datetime as dt, json, os, re, shutil, sys, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hrrr, conus, fields as FX  # noqa: E402

BUCKET = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"
HOURS = 24            # F01-F24 for now: 2.5 km temperature is ~2.1 MB per hour in conus-v1 and GitHub Pages caps the site at 1 GB (NBM posts hourly to F36+; extend with R2)
KEEP = 2              # the two newest complete runs (enough for cycle comparison)
UTC = dt.timezone.utc
PROB_EXTRA = "prob >0.254:prob fcst 255/255"
TERMS = ("NOAA/NWS National Blend of Models output is a U.S. Government work with no copyright restriction in the United States; "
         "NOAA Open Data Dissemination (AWS Open Data, Registry of Open Data 'noaa-nbm') states no restrictions on use or redistribution. "
         "Attribute NOAA/NWS; do not imply NOAA endorsement.")


def url_of(cycle, fh): return f"{BUCKET}/blend.{cycle:%Y%m%d}/{cycle:%H}/core/blend.t{cycle:%H}z.core.f{fh:03d}.co.grib2"


def run_rows(cycle):
    cache = {}
    def rows_of(fh):
        if fh not in cache: u = url_of(cycle, fh); cache[fh] = (u, hrrr.idx_rows(u))
        return cache[fh]
    return rows_of


def read_t2m(url, rows, fh, rows_of):
    k, v, s = FX._get(url, rows, "TMP", "2 m above ground", f"{fh} hour fcst",
                      lambda k: FX._param(k, 0, 0, 0) and k["productDefinitionTemplateNumber"] == 0 and k["typeOfLevel"] == "heightAboveGround"
                      and k["level"] == 2 and FX._instant(k, fh) and k["units"] == "K", fh)
    return k, (v - 273.15) * 9 / 5 + 32, [s]


def _pop(url, rows, fh, hours):
    a = fh - hours
    def ok(k):
        return (FX._param(k, 0, 1, 8) and k["productDefinitionTemplateNumber"] == 9 and k["probabilityType"] == 1
                and k["scaledValueOfUpperLimit"] is not None and abs(k["scaledValueOfUpperLimit"] / 10 ** k["scaleFactorOfUpperLimit"] - 0.254) < 1e-9
                and k["typeOfLevel"] == "surface" and FX._accum(k, a, fh))
    k, v, s = FX._get(url, rows, "APCP", "surface", FX._step_acc(a, fh), ok, fh, extra=PROB_EXTRA)
    if np.nanmax(v) > 100 or np.nanmin(v) < 0: raise RuntimeError("probability outside 0-100%")
    return k, v, [s]


def read_pop1h(url, rows, fh, rows_of): return _pop(url, rows, fh, 1)


def read_pop6h(url, rows, fh, rows_of):
    raise NotImplementedError   # replaced per run: needs the cycle to know the valid time (see pop6h_reader)


def pop6h_reader(cycle):
    def read(url, rows, fh, rows_of):
        valid = cycle + dt.timedelta(hours=fh)
        if fh < 6 or valid.hour % 6: raise FX.NotApplicable("NBM posts 6-h PoP only for windows ending at 00, 06, 12 and 18Z")
        return _pop(url, rows, fh, 6)
    return read


def spec(label, units, scale, step, coarse, read, source, derivation, **extra):
    return {**FX.spec(label, units, scale, step, coarse, read, source, derivation, **extra), "attribution": "NOAA/NWS National Blend of Models", "terms": TERMS}


FIELDS = {
    "t2m": spec("2-m temperature", "degF", 0.1, 5, "corner cell", read_t2m,
                "NOAA NBM core blend.tHHz.core.fFFF.co.grib2, TMP 2 m above ground (GRIB 0/0/0, template 4.0 deterministic, instant, K)",
                "direct: K converted to degF, stored to 0.1 degF; L1/L2 display levels rounded to 0.5 degF"),
    "pop1h": spec("1-h probability of precipitation", "%", 1, 1, "block max", read_pop1h,
                  "NOAA NBM core, APCP surface (N-1)-N hour probability > 0.254 kg m-2 (GRIB 0/1/8, template 4.9, probabilityType 1, upper limit 0.254)",
                  "direct: NBM's own probability of at least 0.01 in of precipitation in the hour ending at the valid time, percent; L1/L2 block maximum",
                  window="previous 1 hour", firstHour=1, note="Chance of at least 0.01 in during the hour."),
    "pop6h": spec("6-h probability of precipitation", "%", 1, 1, "block max", read_pop6h,
                  "NOAA NBM core, APCP surface 6-hour probability > 0.254 kg m-2 for windows ending 00/06/12/18Z (GRIB 0/1/8, template 4.9, probabilityType 1, upper limit 0.254)",
                  "direct: NBM's own probability of at least 0.01 in in the 6 hours ending at the valid time, percent; L1/L2 block maximum",
                  window="previous 6 hours", firstHour=6, note="Chance of at least 0.01 in during the 6 hours; posted for windows ending 00, 06, 12 and 18Z."),
}


def complete_runs(now, look=10):
    """Cycles (newest first) whose F01-F{HOURS} .idx are all posted, with NOAA's posting times."""
    out = []
    for back in range(1, look + 1):
        c = (now - dt.timedelta(hours=back)).replace(minute=0, second=0, microsecond=0)
        q = f"{BUCKET}/?list-type=2&prefix=blend.{c:%Y%m%d}/{c:%H}/core/blend.t{c:%H}z.core.f&max-keys=1000"
        try: x = hrrr.http(q).decode()
        except Exception: continue
        posted = {}
        for key, mod in re.findall(r"<Key>[^<]*core\.f(\d{3})\.co\.grib2\.idx</Key><LastModified>([^<]+)</LastModified>", x):
            posted[int(key)] = mod
        if all(h in posted for h in range(1, HOURS + 1)): out.append((c, {h: posted[h] for h in range(1, HOURS + 1)}))
        if len(out) >= KEEP: break
    return out


def use_nbm(cycle):
    conus.FIELDS = {**FIELDS, "pop6h": {**FIELDS["pop6h"], "read": pop6h_reader(cycle)}}
    conus.MODEL = {"name": "NBM", "slug": "nbm", "firstHour": 1, "domain": "CONUS 2.5 km (full native NBM grid)", "run_rows": run_rows,
                   "attribution": "NOAA/NWS National Blend of Models (NBM) via NOAA Open Data Dissemination (AWS). Rendered by Forecast Ops. Model guidance, not an official forecast."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True); ap.add_argument("--published", default=conus.SITE_URL); ap.add_argument("--now")
    ap.add_argument("--prev"); ap.add_argument("--max-hours", type=int); ap.add_argument("--carry-only", action="store_true"); ap.add_argument("--changed-output")
    a = ap.parse_args()
    if a.carry_only:   # republish the live NBM data unchanged (hash-checked), e.g. when this build's NBM step failed
        conus.PREV = a.prev if a.prev and os.path.isdir(a.prev) else None
        prev = json.loads(hrrr.http(f"{a.published}/data/conus/nbm/index.json"))
        for r in prev.get("runs", []):
            man = json.loads(hrrr.http(f"{a.published}/{r['path']}/manifest.json"))
            for f, sp in man["fields"].items():
                for fh, rec in sp["hours"].items():
                    if "notApplicable" not in rec: conus.carry_hour(a.published, r["path"], f, int(fh), rec, os.path.join(a.site, r["path"]))
            with open(os.path.join(a.site, r["path"], "manifest.json"), "w") as fp: json.dump(man, fp, separators=(",", ":"))
        os.makedirs(os.path.join(a.site, "data", "conus", "nbm"), exist_ok=True)
        with open(os.path.join(a.site, "data", "conus", "nbm", "index.json"), "w") as fp: json.dump(prev, fp, indent=1)
        print("carried", [r["id"] for r in prev.get("runs", [])]); return
    conus.PREV = a.prev if a.prev and os.path.isdir(a.prev) else None
    now = dt.datetime.fromisoformat(a.now).replace(tzinfo=UTC) if a.now else dt.datetime.now(UTC)
    root = os.path.join(a.site, "data", "conus", "nbm")
    try: prev = json.loads(hrrr.http(f"{a.published}/data/conus/nbm/index.json"))
    except Exception: prev = {"runs": []}
    have = {r["id"]: r for r in prev.get("runs", [])}
    t0 = time.time(); keep = complete_runs(now)
    if not keep: raise SystemExit("no complete NBM run posted")
    runs, failures, processed = [], [], 0
    n = a.max_hours or HOURS
    for c, posted in keep:
        use_nbm(c)
        rid = c.strftime("%Y%m%d%H"); rel = f"data/conus/nbm/{rid}/{conus.PRODUCT}"; dest = os.path.join(a.site, rel)
        old = None
        if rid in have:
            try: old = json.loads(hrrr.http(f"{a.published}/{rel}/manifest.json"))
            except Exception: old = None
        try:
            man, p = conus.build_run(c, "hourly", n, True, {h: t for h, t in posted.items() if h <= n}, dest, rel, old, a.published)
            processed += p
            runs.append(conus.summary(man, rel))
        except Exception as e:
            failures.append({"id": rid, "error": str(e)[:300]}); shutil.rmtree(dest, ignore_errors=True)
    if not runs: raise SystemExit("nothing publishable: " + json.dumps(failures))
    index = {"schema": 1, "product": conus.PRODUCT, "model": "NBM", "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "runs": runs, "failures": failures,
             "retention": f"the {KEEP} newest NBM core runs with F01-F{HOURS:02d} posted", "build": {"seconds": round(time.time() - t0, 1), "hoursProcessed": processed, "filesCarried": dict(conus.CARRY_STATS)}}
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "index.json"), "w") as fp: json.dump(index, fp, indent=1)
    if a.changed_output:
        changed = processed > 0 or sorted(r["id"] for r in runs) != sorted(have)
        with open(a.changed_output, "a") as fp: fp.write(f"nbm_changed={'true' if changed else 'false'}\n")
    print(json.dumps({"failures": failures, "build": index["build"]}), [(r["id"], r["status"], len(r["hours"])) for r in runs])


if __name__ == "__main__":
    main()
