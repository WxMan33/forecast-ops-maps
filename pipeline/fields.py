"""HRRR CONUS fields for Forecast Ops Model Maps: what each field is, exactly which GRIB messages make it, and how.

Every message is chosen from the .idx by name/level/step, downloaded by byte range, and then CONFIRMED from its own GRIB
metadata (discipline / parameter category / parameter number, level type, statistical processing, step window, units)
before a single value is used. A message that does not match is never published; the hour is left missing.

Verified against live HRRR wrfsfcfNN.grib2 (21 Nov 2026, 15Z f06): see docs/CONUS-PRODUCT-INVENTORY.md for the table.
  REFC   0/16/196  (NCEP local; eccodes shortName refc)  entireAtmosphere, instant, dB  = simulated composite reflectivity
  APCP   0/1/8     (tp)    surface, accum (stat 1), kg m-2; HRRR posts 0-N and (N-1)-N windows
  ASNOW  0/1/29    (NCEP local "Total Snowfall"; eccodes reports shortName/units "unknown")  surface, accum 0-N only, m
  CRAIN/CSNOW/CICEP/CFRZR  0/1/33, 36, 35, 34 (WMO categorical rain/snow/ice pellets/freezing rain; crain, csnow, cicep, cfrzr)  surface, instant, code table 4.222 (0/1)
"""
import hashlib
import numpy as np
from eccodes import codes_new_from_message, codes_get, codes_get_values, codes_release

import hrrr

MM_PER_IN = 25.4
IN_PER_M = 1000 / 25.4
NOAA_TERMS = ("NOAA/NCEP HRRR output is a U.S. Government work with no copyright restriction in the United States; NOAA Open Data "
              "Dissemination (AWS Open Data) states no restrictions on use or redistribution. Attribute NOAA/NCEP; do not imply NOAA endorsement.")
KEYS = ("discipline", "parameterCategory", "parameterNumber", "shortName", "typeOfLevel", "level", "stepType", "startStep", "endStep",
        "stepUnits", "units", "dataDate", "dataTime")


def decode(buf):
    """Values (NaN for GRIB missing) plus the identity keys used to confirm the message, and the grid keys."""
    g = codes_new_from_message(buf)
    try:
        k = {n: codes_get(g, n) for n in KEYS + hrrr.GRID_KEYS}
        for n in ("typeOfStatisticalProcessing", "productDefinitionTemplateNumber", "probabilityType", "scaledValueOfUpperLimit", "scaleFactorOfUpperLimit"):
            try: k[n] = codes_get(g, n)   # the last four identify NBM's deterministic and probability products
            except Exception: k[n] = None
        v = hrrr.grid_values(g).astype(np.float64)   # geographic order, also for alternative row scanning (NBM)
        if codes_get(g, "numberOfMissing"): v[v == codes_get(g, "missingValue")] = np.nan
        return k, v
    finally:
        codes_release(g)


def _instant(k, fh): return k["stepType"] == "instant" and k["stepUnits"] == 1 and k["startStep"] == fh and k["endStep"] == fh
def _accum(k, a, b): return k["stepType"] == "accum" and k["stepUnits"] == 1 and k["typeOfStatisticalProcessing"] == 1 and k["startStep"] == a and k["endStep"] == b
def _param(k, d, c, n): return (k["discipline"], k["parameterCategory"], k["parameterNumber"]) == (d, c, n)


def _get(url, rows, var, level, step, check, fh, extra=""):
    """One message by .idx line, confirmed from its GRIB keys; returns (keys, values, source record). `extra` is the .idx
    qualifier after the step ("" for a plain field; NBM writes e.g. "prob >0.254:prob fcst 255/255" or "ens std dev")."""
    steps = step if isinstance(step, list) else [step]
    levels = level if isinstance(level, (list, tuple)) else [level]   # the same level can be worded differently (RRFS: "... (considered as a single layer)")
    cand = [r for r in rows if r["var"] == var and r["level"] in levels and r["step"] in steps and r.get("extra", "") == extra]
    if not cand: raise RuntimeError(f"{var} {level} '{step}' not in the index")
    for r in cand:
        buf = hrrr.http(url, (r["start"], r["end"])); k, v = decode(buf)
        if check(k):
            return k, v, {"idx": f'{var}:{level}:{r["step"]}' + (f":{extra}" if extra else ""), "range": [r["start"], r["end"]], "bytes": len(buf), "sha256": hashlib.sha256(buf).hexdigest(),
                          "grib": {x: k[x] for x in ("discipline", "parameterCategory", "parameterNumber", "shortName", "typeOfLevel", "level",
                                                     "stepType", "typeOfStatisticalProcessing", "startStep", "endStep", "units")}}
        raise RuntimeError(f"{var} {step}: GRIB identity does not match ({ {x: k[x] for x in ('discipline', 'parameterCategory', 'parameterNumber', 'typeOfLevel', 'stepType', 'startStep', 'endStep', 'units')} })")


# ---- per-field readers: (url, rows of this hour's .idx, fh, rows_of(fh) for another hour of the same run) -> (keys, values in display units, sources)
def read_t2m(url, rows, fh, rows_of):
    k, v, s = _get(url, rows, "TMP", "2 m above ground", f"{fh} hour fcst" if fh else "anl",
                   lambda k: k["shortName"] == "2t" and k["typeOfLevel"] == "heightAboveGround" and k["level"] == 2 and k["stepType"] == "instant" and k["units"] == "K", fh)
    return k, (v - 273.15) * 9 / 5 + 32, [s]


def read_d2m(url, rows, fh, rows_of):
    k, v, s = _get(url, rows, "DPT", "2 m above ground", f"{fh} hour fcst" if fh else "anl",
                   lambda k: _param(k, 0, 0, 6) and k["typeOfLevel"] == "heightAboveGround" and k["level"] == 2 and _instant(k, fh) and k["units"] == "K", fh)
    return k, (v - 273.15) * 9 / 5 + 32, [s]


MPH_PER_MS = 2.2369362920544


def read_gust(url, rows, fh, rows_of):
    k, v, s = _get(url, rows, "GUST", "surface", f"{fh} hour fcst" if fh else "anl",
                   lambda k: _param(k, 0, 2, 22) and k["typeOfLevel"] == "surface" and _instant(k, fh) and k["units"] == "m s**-1", fh)
    return k, np.where(v >= 0, v * MPH_PER_MS, np.nan), [s]


def read_frzr_run(url, rows, fh, rows_of):
    if fh < 1: raise NotApplicable("no accumulation at the analysis hour")
    k, v, s = _get(url, rows, "FRZR", "surface", _step_acc(0, fh),
                   lambda k: _param(k, 0, 1, 225) and k["typeOfLevel"] == "surface" and _accum(k, 0, fh) and k["units"] == "kg m**-2", fh)
    return k, np.where(v >= 0, v / MM_PER_IN, np.nan), [s]


def read_refc(url, rows, fh, rows_of):
    k, v, s = _get(url, rows, "REFC", ["entire atmosphere", "entire atmosphere (considered as a single layer)"], f"{fh} hour fcst" if fh else "anl",
                   lambda k: _param(k, 0, 16, 196) and k["typeOfLevel"] == "atmosphere" and _instant(k, fh) and k["units"] == "dB", fh)
    return k, v, [s]


def _step_acc(a, b):
    """The .idx wording of an a-b hour accumulation. wgrib2 writes whole days as days: at F24 the run total is
    '0-1 day acc fcst', at F48 '0-2 day acc fcst' (21 Nov 2026 12Z). The GRIB step keys are checked either way."""
    out = [f"{a}-{b} hour acc fcst"]
    if a % 24 == 0 and b % 24 == 0: out.append(f"{a // 24}-{b // 24} day acc fcst")
    return out


def read_apcp1h(url, rows, fh, rows_of):
    if fh < 1: raise NotApplicable("no accumulation at the analysis hour")
    k, v, s = _get(url, rows, "APCP", "surface", _step_acc(fh - 1, fh),
                   lambda k: _param(k, 0, 1, 8) and k["typeOfLevel"] == "surface" and _accum(k, fh - 1, fh) and k["units"] == "kg m**-2", fh)
    return k, np.where(v >= 0, v / MM_PER_IN, np.nan), [s]


def read_apcp_run(url, rows, fh, rows_of):
    if fh < 1: raise NotApplicable("no accumulation at the analysis hour")
    k, v, s = _get(url, rows, "APCP", "surface", _step_acc(0, fh),
                   lambda k: _param(k, 0, 1, 8) and k["typeOfLevel"] == "surface" and _accum(k, 0, fh) and k["units"] == "kg m**-2", fh)
    return k, np.where(v >= 0, v / MM_PER_IN, np.nan), [s]


def _asnow(url, rows, fh):
    # eccodes cannot name NCEP's local ASNOW (shortName/units "unknown"): identity is the parameter triple 0/1/29, units m (NCEP table 4.2-0-1)
    return _get(url, rows, "ASNOW", "surface", _step_acc(0, fh),
                lambda k: _param(k, 0, 1, 29) and k["typeOfLevel"] == "surface" and _accum(k, 0, fh), fh)


def read_asnow_run(url, rows, fh, rows_of):
    if fh < 1: raise NotApplicable("no accumulation at the analysis hour")
    k, v, s = _asnow(url, rows, fh)
    return k, np.where(v >= 0, v * IN_PER_M, np.nan), [s]


NEG_TOL_IN = 0.05   # packing noise between two run totals; anything more negative is an error, not noise


def read_asnow1h(url, rows, fh, rows_of):
    """ASNOW(0-N) - ASNOW(0-(N-1)) from the same run: HRRR posts snowfall only as a run total."""
    if fh < 1: raise NotApplicable("no accumulation at the analysis hour")
    k, v, s = _asnow(url, rows, fh)
    if fh == 1: return k, np.where(v >= 0, v * IN_PER_M, np.nan), [s]
    purl, prows = rows_of(fh - 1)
    k0, v0, s0 = _asnow(purl, prows, fh - 1)
    d = (v - v0) * IN_PER_M
    worst = float(np.nanmin(d)) if np.isfinite(d).any() else 0.0
    if worst < -NEG_TOL_IN: raise RuntimeError(f"snowfall run total decreased by {-worst:.2f} in between f{fh - 1:02d} and f{fh:02d}")
    s["derivation"] = {"previous": s0, "largestDecreaseClippedIn": round(-min(worst, 0.0), 4)}
    return k, np.where(np.isfinite(d), np.maximum(d, 0.0), np.nan), [s, s0]


PTYPE_CODES = {0: "none", 1: "rain", 2: "snow", 3: "ice pellets", 4: "freezing rain"}


def read_ptype(url, rows, fh, rows_of):
    """One category per cell from HRRR's four yes/no flags; freezing rain > ice pellets > snow > rain when several are set."""
    if fh < 1: raise NotApplicable("the HRRR analysis hour carries no precipitation-type diagnosis (all four flags are 0 everywhere)")
    return ptype_flags(url, rows, fh)


def ptype_flags(url, rows, fh):
    """The four categorical flags combined (also at F00, for models whose analysis sets them, e.g. RRFS)."""
    lvl, st = "surface", f"{fh} hour fcst" if fh else "anl"
    flags, srcs, k = {}, [], None
    for var, num, code in (("CRAIN", 33, 1), ("CSNOW", 36, 2), ("CICEP", 35, 3), ("CFRZR", 34, 4)):
        k, v, s = _get(url, rows, var, lvl, st, lambda k, num=num: _param(k, 0, 1, num) and k["typeOfLevel"] == "surface" and _instant(k, fh), fh)
        if np.nanmax(np.where(np.isfinite(v), v, 0)) > 1 or np.nanmin(np.where(np.isfinite(v), v, 0)) < 0: raise RuntimeError(f"{var} is not a 0/1 flag")
        flags[code] = v; srcs.append(s)
    out = np.zeros_like(flags[1])
    for code in (1, 2, 3, 4): out = np.where(flags[code] >= 0.5, code, out)      # later codes win: precedence rain < snow < ice pellets < freezing rain
    miss = np.zeros(out.shape, bool)
    for v in flags.values(): miss |= ~np.isfinite(v)
    return k, np.where(miss, np.nan, out), srcs


class NotApplicable(Exception):
    """The field does not exist at this forecast hour (e.g. accumulations at f00); published as 'not applicable', not missing."""


def spec(label, units, scale, step, coarse, read, source, derivation, **extra):
    return {"label": label, "units": units, "scale": scale, "displayStep": step, "coarse": coarse, "read": read, "source": source,
            "derivation": derivation, "attribution": "NOAA/NCEP HRRR", "terms": NOAA_TERMS, **extra}


FIELDS = {
    "t2m": spec("2-m temperature", "degF", 0.1, 5, "corner cell", read_t2m,
                "NOAA HRRR wrfsfcfNN.grib2, TMP 2 m above ground (GRIB shortName 2t, heightAboveGround 2, instant, K)",
                "direct: K converted to degF, stored to 0.1 degF; L1/L2 display levels rounded to 0.5 degF"),
    "d2m": spec("2-m dew point", "degF", 0.1, 5, "corner cell", read_d2m,
                "NOAA HRRR wrfsfcfNN.grib2, DPT 2 m above ground (GRIB 0/0/6, heightAboveGround 2, instant, K)",
                "direct: K converted to degF, stored to 0.1 degF; L1/L2 rounded to 0.5 degF"),
    "gust": spec("Surface wind gust", "mph", 0.1, 10, "block max", read_gust,
                 "NOAA HRRR wrfsfcfNN.grib2, GUST surface (GRIB 0/2/22, instant, m s-1)",
                 "direct: m/s converted to mph, stored to 0.1 mph; L1/L2 block maximum to 1 mph; HRRR's instantaneous gust diagnostic at the valid time, not an hourly maximum"),
    "frzr_run": spec("Freezing rain since run start", "in", 0.001, 10, "block max", read_frzr_run,
                     "NOAA HRRR wrfsfcfNN.grib2, FRZR surface 0-N hour accumulation (GRIB 0/1/225, NCEP local 'Freezing Rain', accum, kg m-2)",
                     "direct: liquid equivalent of freezing rain from the run start, mm converted to inches; not ice accretion on surfaces (no ratio applied); L1/L2 block maximum to 0.01 in",
                     window="run start to valid time", firstHour=1, note="Liquid equivalent of freezing rain, not ice thickness on surfaces."),
    "refc": spec("Simulated composite reflectivity", "dBZ", 0.1, 10, "block max", read_refc,
                 "NOAA HRRR wrfsfcfNN.grib2, REFC entire atmosphere (GRIB 0/16/196, instant, dB)",
                 "direct: model-simulated column-maximum reflectivity, stored to 0.1 dBZ; L1/L2 the block maximum to 1 dBZ",
                 simulated=True, note="Model-simulated, not radar."),
    "ptype": spec("Precipitation type", "category", 1, 1, "block max", read_ptype,
                  "NOAA HRRR wrfsfcfNN.grib2, CRAIN/CSNOW/CICEP/CFRZR surface (GRIB 0/1/33, 36, 35, 34; instant; code table 4.222)",
                  "one category per cell from the four yes/no flags; when several are set, freezing rain > ice pellets > snow > rain; "
                  "L1/L2 take the highest-precedence type in the block", codes=PTYPE_CODES, firstHour=1,
                  note="Categorical type where the model is precipitating at the valid time; not an amount."),
    "apcp1h": spec("1-h precipitation", "in", 0.001, 10, "block max", read_apcp1h,
                   "NOAA HRRR wrfsfcfNN.grib2, APCP surface (N-1)-N hour accumulation (GRIB 0/1/8, accum, kg m-2)",
                   "direct: liquid equivalent in the hour ending at the valid time, mm converted to inches; L1/L2 block maximum to 0.01 in",
                   window="previous 1 hour", firstHour=1),
    "apcp_run": spec("Precipitation since run start", "in", 0.001, 10, "block max", read_apcp_run,
                     "NOAA HRRR wrfsfcfNN.grib2, APCP surface 0-N hour accumulation (GRIB 0/1/8, accum, kg m-2)",
                     "direct: liquid equivalent from the run's start to the valid time, mm converted to inches; L1/L2 block maximum to 0.01 in",
                     window="run start to valid time", firstHour=1),
    "asnow1h": spec("1-h snowfall (HRRR)", "in", 0.01, 10, "block max", read_asnow1h,
                    "NOAA HRRR wrfsfcfNN.grib2, ASNOW surface 0-N and 0-(N-1) hour accumulations of the same run (GRIB 0/1/29, NCEP local 'Total Snowfall', m)",
                    "derived: ASNOW(0-N) minus ASNOW(0-(N-1)), m converted to inches; decreases up to 0.05 in (packing noise) are set to 0, larger ones fail the hour; "
                    "HRRR's own variable-density snowfall, not a fixed 10:1 ratio", window="previous 1 hour", firstHour=1),
    "asnow_run": spec("Snowfall since run start (HRRR)", "in", 0.01, 10, "block max", read_asnow_run,
                      "NOAA HRRR wrfsfcfNN.grib2, ASNOW surface 0-N hour accumulation (GRIB 0/1/29, NCEP local 'Total Snowfall', m)",
                      "direct: m converted to inches; HRRR's own variable-density snowfall, not a fixed 10:1 ratio; L1/L2 block maximum to 0.1 in",
                      window="run start to valid time", firstHour=1),
}


def encode(field, vals, table=None):
    """Display-unit floats -> int16 at the field's scale; NaN -> missing sentinel."""
    q = np.round(np.asarray(vals, np.float64) / (table or FIELDS)[field]["scale"])
    return np.where(np.isfinite(q), np.clip(q, -32767, 32767), hrrr.MISS16).astype("<i2")
