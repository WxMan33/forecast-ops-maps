#!/usr/bin/env python3
"""ECMWF IFS (open data, 0.25 degree) over the CONUS for Forecast Ops Model Maps, in the conus-v1 format (pipeline/conus.py
does the tiling, label points, carry-forward and manifests; this file says which ECMWF messages make each field, which
cells are published and which runs to keep).

Source: ECMWF open data on its AWS mirror (s3://ecmwf-forecasts, eu-central-1), the same files ECMWF serves at
data.ecmwf.int: YYYYMMDD/HHz/ifs/0p25/oper/YYYYMMDDHH0000-<step>h-oper-fc.grib2 with a JSON-lines .index (param, levtype,
step, _offset, _length) used to fetch only the messages needed, by byte range. 00Z and 12Z runs, steps 0-120 every 3 h.

Grid: the global 0.25 degree regular lat-lon grid (1440 x 721, first point 90N 180W, rows north to south). Published: the
native cells from 20N to 55N and 135W to 60W (cell centres; 301 x 141 cells), which covers the HRRR footprint (21.1-52.6N,
134.1-60.9W) with margin. Cropped, never resampled; rows are stored south to north like the Lambert grids. Every cell
centre is checked against GRIB's own latitudes/longitudes and eccodes' nearest point (conus.georef_ll).

Each message is confirmed from its own GRIB keys (checked on the 23 Nov 2026 12Z run):
  2t     0/0/0   heightAboveGround 2, instant, K
  2d     0/0/6   heightAboveGround 2, instant, K
  10fg   0/2/22  heightAboveGround 10, max (statistical processing 2) over (N-1)-N h: steps 3-90 ("since previous post-processing")
  10fg3  0/2/22  heightAboveGround 10, max over (N-3)-N h: steps 93-120 (10fg is not posted there)
  tp     0/1/193 surface, accum 0-N h, m (liquid water equivalent)
  sf     0/1/198 surface, accum 0-N h, m of water equivalent (ECMWF gives snowfall ONLY as water equivalent)
  ptype  0/1/19  surface, instant, WMO code table 4.201 (ECMWF uses 0 for no precipitation)
  sp     0/3/0   surface, instant, Pa (Kuchera snowfall: which pressure levels are above the ground)
  t      0/0/0   isobaricInhPa 1000, 925, 850, 700, 600, 500, instant, K (Kuchera snowfall: the warmest temperature aloft)
At step 0 the accumulations and gust messages are constant zero fields (0-0 h windows): published as not applicable.
Not offered: 1-h fields (the steps are 3-hourly), reflectivity, freezing-rain accumulation, PoP (not in the open data).

Usage: python pipeline/ecmwf.py --site SITE [--published URL] [--prev DIR] [--max-hours N] [--pack] [--now ISO] [--changed-output F]
"""
import argparse, concurrent.futures as cf, datetime as dt, hashlib, json, os, random, re, shutil, sys, threading, time, urllib.error, urllib.request
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hrrr, conus, fields as FX  # noqa: E402

BUCKET = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"
CYCLES = (0, 12)                    # 00Z and 12Z (the 06/18Z open-data runs stop at 90 h)
STEPS = list(range(0, 121, 3))      # 0-120 h every 3 h: 41 steps
KEEP = 2                            # the two newest runs (enough for the cycle comparison)
GIVE_UP_H = 12                      # a run still missing steps this long after its cycle time is final with gaps
CROP = {"lat": [20.0, 55.0], "lon": [-135.0, -60.0]}   # cell centres, degrees: covers the HRRR CONUS footprint
SNOW_RATIO = 10                     # stated snow-to-liquid ratio applied to ECMWF's snowfall water equivalent
# Kuchera snow-to-liquid ratio (the method of Evan Kuchera, as used in common NWP snowfall displays): from Tmax, the warmest
# temperature (K) between the ground and 500 hPa, ratio = 12 + (271.16 - Tmax) when Tmax <= 271.16 K, else 12 + 2 (271.16 - Tmax);
# floored at 0 (no negative snow). No upper cap is applied.
KUCHERA_T0 = 271.16
KUCHERA_LEVELS = (1000, 925, 850, 700, 600, 500)   # hPa; 2t is the surface level
KUCHERA_FORMULA = ("Tmax = max(2t, t at each of 1000/925/850/700/600/500 hPa whose pressure is <= the cell's surface pressure sp), K; "
                   "ratio = 12 + 2*(271.16 - Tmax) if Tmax > 271.16 else 12 + (271.16 - Tmax); ratio floored at 0, no upper cap")
UTC = dt.timezone.utc
SRC = "ECMWF IFS open data YYYYMMDDHH0000-<step>h-oper-fc.grib2 (0.25 degree, AWS mirror s3://ecmwf-forecasts)"
CREDIT = "© ECMWF, CC BY 4.0 (open data; cropped and encoded by Forecast Ops)"
TERMS = ("ECMWF open data: Copyright © ECMWF, licensed under Creative Commons Attribution 4.0 International (CC BY 4.0, "
         "https://creativecommons.org/licenses/by/4.0/). Changes made by Forecast Ops: cropped to native 0.25 degree cells over the CONUS "
         "(never resampled), converted to US units, ECMWF precipitation-type codes grouped into four categories, snowfall water equivalent "
         "shown at a stated 10:1 ratio and, as a second field, at a Kuchera ratio computed from ECMWF temperatures, and encoded as map tiles. ECMWF does not endorse Forecast Ops or this rendering.")
# ECMWF precipitation type (WMO code table 4.201) -> the viewer's categories (fields.PTYPE_CODES: 0 none, 1 rain, 2 snow,
# 3 ice pellets, 4 freezing rain). Codes with no matching category (2 thunderstorm, 4 mixed/ice, 9 graupel, 10 hail, ...)
# and any non-integer value are left MISSING, never shown as rain.
PTYPE_MAP = {0: 0, 1: 1, 11: 1, 5: 2, 6: 2, 7: 2, 8: 3, 3: 4, 12: 4}
PTYPE_SOURCE = {0: "no precipitation", 1: "rain", 3: "freezing rain", 5: "snow", 6: "wet snow", 7: "mixture of rain and snow", 8: "ice pellets",
                11: "drizzle", 12: "freezing drizzle"}


def http(url, rng=None, tries=40, timeout=60):
    """GET (optionally a byte range) from the ECMWF mirror. The bucket throttles bursts with 503 SlowDown (about half of all
    requests from the sandbox on 24 Nov 2026, even one at a time): retry those after a short random pause, many times,
    instead of hrrr.http's long exponential back-off. 403/404 are final."""
    h = dict(hrrr.UA)
    if rng: h["Range"] = f"bytes={rng[0]}-{rng[1]}" if rng[1] is not None else f"bytes={rng[0]}-"
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r: return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404) or k == tries - 1: raise
        except Exception:
            if k == tries - 1: raise
        time.sleep(random.uniform(0.3, 1.5) * min(k + 1, 5))
    raise RuntimeError("unreachable")


def url_of(cycle, fh): return f"{BUCKET}/{cycle:%Y%m%d}/{cycle:%H}z/ifs/0p25/oper/{cycle:%Y%m%d%H}0000-{fh}h-oper-fc.grib2"


def index_rows(url):
    """ECMWF's JSON-lines .index as the rows fields._get reads (var = param, level = levtype[:levelist], step = step)."""
    out = []
    for n, line in enumerate(l for l in http(url[: -len(".grib2")] + ".index").decode().splitlines() if l.strip()):
        d = json.loads(line); off, ln = int(d["_offset"]), int(d["_length"])
        out.append({"n": n + 1, "start": off, "end": off + ln - 1, "var": d["param"], "level": d["levtype"] + (f":{d['levelist']}" if d.get("levelist") else ""),
                    "step": str(d["step"]), "extra": ""})
    return out


def run_rows(cycle):
    cache = {}
    def rows_of(fh):
        if fh not in cache: u = url_of(cycle, fh); cache[fh] = (u, index_rows(u))
        return cache[fh]
    rows_of.kuchera = {}; rows_of.lock = threading.Lock()   # per-run cache of the Kuchera inputs of each step (shared by the worker threads)
    return rows_of


# ---- readers: (url, rows of this step's index, fh, rows_of) -> (GRIB keys, full-grid values in display units, sources)
def _sfc(url, rows, param, fh, check):
    return FX._get(url, rows, param, "sfc", str(fh), check, fh, http=http)


def _hag(k, level): return k["typeOfLevel"] == "heightAboveGround" and k["level"] == level


def read_t2m(url, rows, fh, rows_of):
    k, v, s = _sfc(url, rows, "2t", fh, lambda k: FX._param(k, 0, 0, 0) and _hag(k, 2) and FX._instant(k, fh) and k["units"] == "K")
    return k, (v - 273.15) * 9 / 5 + 32, [s]


def read_d2m(url, rows, fh, rows_of):
    k, v, s = _sfc(url, rows, "2d", fh, lambda k: FX._param(k, 0, 0, 6) and _hag(k, 2) and FX._instant(k, fh) and k["units"] == "K")
    return k, (v - 273.15) * 9 / 5 + 32, [s]


def gust_param(rows, fh):
    """(param, window hours) ECMWF posts for this step: 10fg (1 h) through F90, 10fg3 (3 h) after."""
    for param, w in (("10fg", 1), ("10fg3", 3)):
        if any(r["var"] == param and r["level"] == "sfc" and r["step"] == str(fh) for r in rows): return param, w
    raise RuntimeError(f"no 10fg or 10fg3 at step {fh}")


def read_gust(url, rows, fh, rows_of):
    if fh < 1: raise FX.NotApplicable("no gust window at the analysis step (ECMWF posts a constant zero field)")
    param, w = gust_param(rows, fh)
    k, v, s = _sfc(url, rows, param, fh, lambda k: FX._param(k, 0, 2, 22) and _hag(k, 10) and k["stepType"] == "max" and k["typeOfStatisticalProcessing"] == 2
                   and k["stepUnits"] == 1 and k["startStep"] == fh - w and k["endStep"] == fh and k["units"] == "m s**-1")
    s["window"] = f"maximum over the {w} h ending at the valid time ({fh - w}-{fh} h)"
    return k, np.where(v >= 0, v * FX.MPH_PER_MS, np.nan), [s]


def _accum(k, fh, num, units): return FX._param(k, 0, 1, num) and k["typeOfLevel"] == "surface" and FX._accum(k, 0, fh) and k["units"] == units


def read_apcp_run(url, rows, fh, rows_of):
    if fh < 1: raise FX.NotApplicable("no accumulation at the analysis step")
    k, v, s = _sfc(url, rows, "tp", fh, lambda k: _accum(k, fh, 193, "m"))
    return k, np.where(v >= 0, v * FX.IN_PER_M, np.nan), [s]


def read_asnow_run(url, rows, fh, rows_of):
    if fh < 1: raise FX.NotApplicable("no accumulation at the analysis step")
    k, v, s = _sfc(url, rows, "sf", fh, lambda k: _accum(k, fh, 198, "m of water equivalent"))
    return k, np.where(v >= 0, v * FX.IN_PER_M * SNOW_RATIO, np.nan), [s]


def kuchera_ratio(t2, sp, temps):
    """(ratio, Tmax) per cell. t2: 2-m temperature (K); sp: surface pressure (Pa); temps: {hPa: temperature (K)}. A pressure
    level counts only where it is at or above the ground (level pressure <= sp): ECMWF extrapolates levels below the ground."""
    tmax = np.array(t2, np.float64, copy=True)
    for p, t in temps.items():
        t = np.asarray(t, np.float64); use = (p * 100.0 <= np.asarray(sp)) & np.isfinite(t)
        tmax = np.where(use & (t > tmax), t, tmax)
    ratio = np.where(tmax > KUCHERA_T0, 12 + 2 * (KUCHERA_T0 - tmax), 12 + (KUCHERA_T0 - tmax))
    return np.maximum(ratio, 0.0), tmax


def kuchera_interval(sf_now, sf_before, ratio):
    """Inches of snow in one 3-h interval: the snowfall water equivalent gained (m, both run totals; decreases are packing
    noise -> 0) in inches of liquid, times the Kuchera ratio of the profile at the END of the interval."""
    return np.maximum(np.asarray(sf_now) - np.asarray(sf_before), 0.0) * FX.IN_PER_M * ratio


def kuchera_total(steps):
    """Run total (inches) from [(sf, ratio)] for the steps 3, 6, ... N in order (sf: run-total water equivalent, m)."""
    tot, prev = None, 0.0
    for sf, ratio in steps:
        inc = kuchera_interval(sf, prev, ratio); tot = inc if tot is None else tot + inc; prev = sf
    return tot


def _uncrop(vals, k):
    """A cropped array back on the full message grid (NaN elsewhere), for conus.process_hour to crop again."""
    g = conus.grid_of(k); full = np.full((k["Nj"], k["Ni"]), np.nan)
    full[(g["j0"] + g["rowStep"] * np.arange(g["ny"]))[:, None], np.arange(g["i0"], g["i0"] + g["nx"])[None, :]] = vals
    return full


def kuchera_step(rows_of, s):
    """The published cells' Kuchera inputs at step s (cached per run): sf (m), ratio, Tmax, the sf keys and the sources."""
    with rows_of.lock: lk = rows_of.kuchera.setdefault(("lock", s), threading.Lock())
    with lk:
        if s in rows_of.kuchera: return rows_of.kuchera[s]
        url, rows = rows_of(s)
        k, sf, s_sf = _sfc(url, rows, "sf", s, lambda k: _accum(k, s, 198, "m of water equivalent"))
        g = conus.grid_of(k); c = lambda v: conus.crop(v, g)
        k2, t2, s_t2 = _sfc(url, rows, "2t", s, lambda k: FX._param(k, 0, 0, 0) and _hag(k, 2) and FX._instant(k, s) and k["units"] == "K")
        k3, sp, s_sp = _sfc(url, rows, "sp", s, lambda k: FX._param(k, 0, 3, 0) and k["typeOfLevel"] == "surface" and FX._instant(k, s) and k["units"] == "Pa")
        temps, srcs = {}, [s_sf, s_t2, s_sp]
        for p in KUCHERA_LEVELS:
            kp, tp, s_t = FX._get(url, rows, "t", f"pl:{p}", str(s), lambda k, p=p: FX._param(k, 0, 0, 0) and k["typeOfLevel"] == "isobaricInhPa" and k["level"] == p
                                  and FX._instant(k, s) and k["units"] == "K", s, http=http)
            temps[p] = c(tp); srcs.append(s_t)
            if conus.grid_of(kp) != g: raise RuntimeError(f"t {p} hPa at step {s} is on another grid")
        if conus.grid_of(k2) != g or conus.grid_of(k3) != g: raise RuntimeError(f"Kuchera inputs at step {s} are on different grids")
        ratio, tmax = kuchera_ratio(c(t2), c(sp), temps)
        out = rows_of.kuchera[s] = {"sf": c(sf), "ratio": ratio, "tmax": tmax, "k": k, "sources": srcs}
        return out


def read_asnowk_run(url, rows, fh, rows_of):
    """Kuchera run total at step fh, rebuilt from the GRIB every time (never from published tiles): every 3-h step from F03
    to fh is read (cached per run), so a partially published run or carried hours cannot change the result. A step
    missing from the mirror fails this hour (published as missing, retried on the next build)."""
    if fh < 1: raise FX.NotApplicable("no accumulation at the analysis step")
    steps = [s for s in STEPS if 0 < s <= fh]
    if not hasattr(rows_of, "kuchera"): rows_of.kuchera = {}; rows_of.lock = threading.Lock()
    with cf.ThreadPoolExecutor(3) as ex: ins = list(ex.map(lambda s: kuchera_step(rows_of, s), steps))   # steps fetched in parallel, each once per run
    tot = kuchera_total([(x["sf"], x["ratio"]) for x in ins])
    last = ins[-1]; srcs = [dict(x) for x in last["sources"]]
    digest = hashlib.sha256("".join(src["sha256"] for x in ins for src in x["sources"]).encode()).hexdigest()
    srcs[0]["derivation"] = {"method": "kuchera", "steps": steps, "messagesPerStep": len(last["sources"]), "inputsSha256": digest,
                             "ratioAtStep": [round(float(np.nanmin(last["ratio"])), 2), round(float(np.nanmax(last["ratio"])), 2)]}
    return last["k"], _uncrop(tot, last["k"]), srcs


def map_ptype(v):
    """ECMWF code table 4.201 values -> viewer categories; unknown or non-integer codes -> NaN (missing)."""
    v = np.asarray(v, np.float64); r = np.round(v); whole = np.isfinite(v) & (np.abs(v - r) < 1e-6); out = np.full(v.shape, np.nan)
    for code, cat in PTYPE_MAP.items(): out[whole & (r == code)] = cat
    return out


def read_ptype(url, rows, fh, rows_of):
    k, v, s = _sfc(url, rows, "ptype", fh, lambda k: FX._param(k, 0, 1, 19) and k["typeOfLevel"] == "surface" and FX._instant(k, fh))
    out = map_ptype(v); s["unmappedCells"] = int((np.isfinite(v) & ~np.isfinite(out)).sum())
    return k, out, [s]


def spec(label, units, scale, step, coarse, read, source, derivation, **extra):
    return {**FX.spec(label, units, scale, step, coarse, read, source, derivation, **extra), "attribution": CREDIT, "terms": TERMS}


FIELDS = {
    "t2m": spec("2-m temperature", "degF", 0.1, 5, "corner cell", read_t2m, f"{SRC}, 2t (GRIB 0/0/0, heightAboveGround 2, instant, K)",
                "direct: K converted to degF, stored to 0.1 degF; L1/L2 display levels rounded to 0.5 degF"),
    "d2m": spec("2-m dew point", "degF", 0.1, 5, "corner cell", read_d2m, f"{SRC}, 2d (GRIB 0/0/6, heightAboveGround 2, instant, K)",
                "direct: K converted to degF, stored to 0.1 degF; L1/L2 rounded to 0.5 degF"),
    "gust": spec("10-m wind gust, maximum over the preceding period (ECMWF)", "mph", 0.1, 10, "block max", read_gust,
                 f"{SRC}, 10fg at steps 3-90 (max since the previous post-processing step, i.e. the preceding 1 h) and 10fg3 at steps 93-120 "
                 "(max in the preceding 3 h) (GRIB 0/2/22, heightAboveGround 10, statistical processing 2 = maximum, m s-1)",
                 "direct: m/s converted to mph, stored to 0.1 mph; L1/L2 block maximum to 1 mph; the window each hour covers is in its source record",
                 window="preceding 1 h (F03-F90) or 3 h (F93-F120)", firstHour=3,
                 note="ECMWF's maximum 10-m gust in the hour before the valid time through F90, and in the 3 hours before it from F93 (ECMWF posts only 3-h maxima there); not an instantaneous gust."),
    "ptype": spec("Precipitation type (ECMWF)", "category", 1, 1, "block max", read_ptype, f"{SRC}, ptype (GRIB 0/1/19, surface, instant, WMO code table 4.201)",
                  "ECMWF codes grouped into the viewer's categories: 1 rain and 11 drizzle -> rain; 5 snow, 6 wet snow and 7 mixture of rain and snow -> snow; "
                  "8 ice pellets -> ice pellets; 3 freezing rain and 12 freezing drizzle -> freezing rain; 0 -> none; any other code (2, 4, 9 graupel, 10 hail, ...) "
                  "or non-integer value is left missing; L1/L2 take the highest category in the block (freezing rain > ice pellets > snow > rain)",
                  codes=FX.PTYPE_CODES, sourceCodes={str(c): f"{n} -> {FX.PTYPE_CODES[PTYPE_MAP[c]]}" for c, n in PTYPE_SOURCE.items()}, firstHour=0,
                  note="ECMWF's precipitation type at the valid time, grouped: drizzle as rain, wet snow and rain-snow mixtures as snow, freezing drizzle as freezing rain."),
    "apcp_run": spec("Precipitation since run start", "in", 0.001, 10, "block max", read_apcp_run, f"{SRC}, tp (GRIB 0/1/193, surface, accum 0-N h, m)",
                     "direct: liquid water equivalent from the run's start to the valid time, m converted to inches; L1/L2 block maximum to 0.01 in",
                     window="run start to valid time", firstHour=3),
    "asnow_run": spec("Snowfall since run start (ECMWF, 10:1 from water equivalent)", "in", 0.01, 10, "block max", read_asnow_run,
                      f"{SRC}, sf (GRIB 0/1/198, surface, accum 0-N h, m of water equivalent)",
                      f"derived: ECMWF provides snowfall only as water equivalent; m converted to inches and multiplied by a fixed {SNOW_RATIO}:1 snow-to-liquid ratio "
                      "(not a snow-density forecast); L1/L2 block maximum to 0.1 in", window="run start to valid time", firstHour=3, snowRatio=SNOW_RATIO,
                      note=f"ECMWF gives snowfall as water equivalent only: shown at a fixed {SNOW_RATIO}:1 ratio, not ECMWF's own snow depth."),
    "asnowk_run": spec("Snowfall since run start (ECMWF, Kuchera ratio from water equivalent)", "in", 0.01, 10, "block max", read_asnowk_run,
                       f"{SRC}, at every 3-h step from F03 to the valid time: sf (GRIB 0/1/198, surface, accum 0-N h, m of water equivalent), 2t (GRIB 0/0/0, "
                       "heightAboveGround 2, K), sp (GRIB 0/3/0, surface, Pa) and t (GRIB 0/0/0, isobaricInhPa 1000/925/850/700/600/500, K)",
                       "derived: for each 3-h interval, the snowfall water equivalent gained (sf at its end minus sf at its start, m converted to inches of liquid, "
                       "decreases set to 0) times the Kuchera snow-to-liquid ratio of the temperature profile at the END of the interval (an approximation: "
                       f"ECMWF gives no profile during the interval); the run total is the sum of the intervals. {KUCHERA_FORMULA}. "
                       "L1/L2 block maximum to 0.1 in", window="run start to valid time", firstHour=3, snowRatio="kuchera", snowRatioFormula=KUCHERA_FORMULA,
                       note="ECMWF gives snowfall as water equivalent only: shown with a Kuchera snow-to-liquid ratio from the warmest temperature between the ground and "
                            "about 18,000 ft (500 hPa) at the end of each 3-h step: colder columns give fluffier snow, near-freezing columns wetter snow. Not ECMWF's own snow depth."),
}


def available(now, days=2):
    """{cycle: {step: LastModified}} for 00/12Z runs of the last `days` days with step 0's index posted (steps in STEPS only)."""
    out = {}
    for back in range(days):
        d = now.date() - dt.timedelta(days=back)
        for hh in CYCLES:
            c = dt.datetime(d.year, d.month, d.day, hh, tzinfo=UTC)
            if c > now: continue
            posted, token = {}, None
            while True:
                q = f"{BUCKET}/?list-type=2&prefix={c:%Y%m%d}/{c:%H}z/ifs/0p25/oper/&max-keys=1000" + (f"&continuation-token={hrrr.urllib.parse.quote(token)}" if token else "")
                try: x = http(q).decode()
                except Exception: break
                for step, mod in re.findall(r"<Key>[^<]*-(\d+)h-oper-fc\.index</Key><LastModified>([^<]+)</LastModified>", x):
                    if int(step) in STEPS: posted[int(step)] = mod
                t = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", x)
                if not t: break
                token = t.group(1)
            if 0 in posted: out[c] = posted
    return out


def plan(avail, now):
    """The KEEP newest runs with step 0 posted (the newest may still be arriving): [(cycle, role, n, complete, posted)]."""
    out = []
    for k, c in enumerate(sorted(avail, reverse=True)[:KEEP]):
        posted = avail[c]; done = set(STEPS) <= set(posted) or (now - c).total_seconds() / 3600 > GIVE_UP_H
        out.append((c, "newest" if k == 0 else "previous", STEPS[-1], done, posted))
    return out


def use_ecmwf(cycle, n=STEPS[-1]):
    conus.FIELDS = FIELDS
    conus.MODEL = {"name": "ECMWF", "slug": "ecmwf", "firstHour": 0, "expected": [h for h in STEPS if h <= n], "crop": CROP, "run_rows": run_rows, "http": http,
                   "domain": "CONUS 0.25 degree (native ECMWF IFS open-data cells, 20-55N 135-60W, cropped, not resampled)",
                   "attribution": f"ECMWF IFS open data. {CREDIT}. Model guidance, not an official forecast."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True); ap.add_argument("--published", default=conus.SITE_URL); ap.add_argument("--now")
    ap.add_argument("--prev"); ap.add_argument("--max-hours", type=int, help="publish steps up to this hour only (local tests)")
    ap.add_argument("--pack", action="store_true"); ap.add_argument("--changed-output")
    a = ap.parse_args()
    if a.pack: conus.PACK, conus.PRODUCT = True, "conus-v1-packed"
    conus.PREV = a.prev if a.prev and os.path.isdir(a.prev) else None
    now = dt.datetime.fromisoformat(a.now).replace(tzinfo=UTC) if a.now else dt.datetime.now(UTC)
    root = os.path.join(a.site, "data", "conus", "ecmwf")
    try: prev = json.loads(hrrr.http(f"{a.published}/data/conus/ecmwf/index.json"))
    except Exception: prev = {"runs": []}
    have = {r["id"]: r for r in prev.get("runs", [])}
    t0 = time.time(); runs = plan(available(now), now)
    if not runs: raise SystemExit("no ECMWF 00/12Z run posted")
    n = min(a.max_hours if a.max_hours is not None else STEPS[-1], STEPS[-1])
    out, failures, processed = [], [], 0
    for c, role, _, done, posted in runs:
        use_ecmwf(c, n)
        rid = c.strftime("%Y%m%d%H"); rel = f"data/conus/ecmwf/{rid}/{conus.PRODUCT}"; dest = os.path.join(a.site, rel); old = None
        if rid in have:
            try: old = json.loads(hrrr.http(f"{a.published}/{rel}/manifest.json"))
            except Exception: old = None
        try:
            man, p = conus.build_run(c, role, n, done, {h: t for h, t in posted.items() if h <= n}, dest, rel, old, a.published); processed += p
            if not p and old and old.get("updatedAt") and {**json.loads(json.dumps(man)), "updatedAt": None} == {**old, "updatedAt": None}:
                man = old   # nothing new for this run: keep the published manifest byte for byte, so the R2 sync skips it (ETag)
                with open(os.path.join(dest, "manifest.json"), "w") as fp: json.dump(man, fp, separators=(",", ":"))
            out.append(conus.summary(man, rel))
        except Exception as e:
            failures.append({"id": rid, "error": str(e)[:300]}); shutil.rmtree(dest, ignore_errors=True)
    if not out: raise SystemExit("nothing publishable: " + json.dumps(failures))
    index = {"schema": 1, "product": conus.PRODUCT, "model": "ECMWF", "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "runs": out, "failures": failures,
             "retention": f"the {KEEP} newest ECMWF IFS 00/12Z open-data runs, steps F00-F{STEPS[-1]} every 3 h, published step by step as they are posted",
             "attribution": CREDIT, "build": {"seconds": round(time.time() - t0, 1), "hoursProcessed": processed, "filesCarried": dict(conus.CARRY_STATS)}}
    if not processed and not failures and out == prev.get("runs"): index = prev   # unchanged: the same bytes, no upload
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "index.json"), "w") as fp: json.dump(index, fp, indent=1)
    if a.changed_output:
        with open(a.changed_output, "a") as fp: fp.write(f"ecmwf_changed={'true' if processed or sorted(r['id'] for r in out) != sorted(have) else 'false'}\n")
    print(json.dumps({"failures": failures, "build": index.get("build")}), [(r["id"], r["role"], r["status"], len(r["hours"])) for r in out])


if __name__ == "__main__":
    main()
