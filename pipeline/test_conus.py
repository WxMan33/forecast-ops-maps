#!/usr/bin/env python3
"""Offline checks for the CONUS tile packager (pipeline/conus.py). Synthetic data only; no network."""
import datetime as dt, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conus  # noqa: E402

UTC = dt.timezone.utc
n = 0


def ok(cond, msg):
    global n
    if not cond: raise SystemExit(f"not ok - {msg}")
    n += 1


# 1. tile coding is exact for any int16, including the missing sentinel and wrap-around differences
rng = np.random.default_rng(7)
for q in [rng.integers(-32768, 32768, (37, 256)).astype(np.int16), np.full((3, 5), conus.MISS, np.int16),
          np.array([[-32768, 32767, -32767, 0, 5, -32768, 1, -1]], np.int16), (np.arange(256 * 256).reshape(256, 256) % 900 - 400).astype(np.int16)]:
    ok((conus.decode_tile(conus.encode_tile(q), *q.shape) == q).all(), f"tile round trip {q.shape}")
smooth = (np.add.outer(np.arange(256), np.arange(256)) // 7).astype(np.int16)
ok(len(conus.encode_tile(smooth)) < 3000, "smooth fields compress (zigzag differences)")

# 2. levels: L1/L2 keep exact native cells (corner rule) or block maxima, in display steps; missing stays missing
q = (np.arange(9 * 11).reshape(9, 11) * 10).astype(np.int16); q[0, 0] = conus.MISS
c1 = conus.coarsen(q, 1, "corner cell", 5)
ok(c1.shape == (5, 6) and c1[0, 0] == conus.MISS and c1[1, 1] == round(q[2, 2] / 5), "L1 corner cells, display steps, missing kept")
m2 = conus.coarsen(q, 2, "block max", 1)
ok(m2.shape == (3, 3) and m2[0, 0] == q[:4, :4].max() and m2[2, 2] == q[8:, 8:].max(), "L2 block maxima include partial edge blocks")
ok(conus.coarsen(q, 0, "corner cell", 5) is q, "L0 is the full-precision native grid")

# 3. tiles cover each level exactly once, at the HRRR grid size
nx, ny = 1799, 1059
for L in conus.LEVELS:
    a = conus.coarsen(np.zeros((ny, nx), np.int16), L, "corner cell", 5)
    names, cells = [], 0
    for name, t in conus.tiles_for(np.zeros((ny, nx), np.int16), L, "corner cell", 5):
        names.append(name); cells += t.size; ok(t.shape[0] <= conus.TILE and t.shape[1] <= conus.TILE, "tile size")
    lx, ly = conus.level_grid(nx, ny, L)
    ok(a.shape == (ly, lx) and cells == lx * ly and len(set(names)) == len(names), f"level {L}: {len(names)} tiles cover {lx}x{ly} cells once")
ok(conus.level_grid(nx, ny, 0) == (1799, 1059) and conus.level_grid(nx, ny, 2) == (450, 265), "level sizes")

# 4. retention: newest cycle as soon as f00 is posted, newest complete run, newest complete 48-h run; at most three
now = dt.datetime(2026, 11, 19, 19, 40, tzinfo=UTC)
c = lambda h, d=19: dt.datetime(2026, 11, d, h, tzinfo=UTC)
av = {c(19): {0: "x"}, c(18): {h: "x" for h in range(16)}, c(17): {h: "x" for h in range(19)}, c(16): {h: "x" for h in range(19)},
      c(12): {h: "x" for h in range(49)}, c(6): {h: "x" for h in range(49)}}
p = conus.plan(av, now)
ok([(x[0].hour, x[1]) for x in p] == [(19, "latest"), (17, "complete"), (12, "extended")], f"retention {[(x[0].hour, x[1]) for x in p]}")
ok(p[0][2] == 18 and p[2][2] == 48 and p[0][3] is False and p[1][3] is True, "hours expected and completeness")
p2 = conus.plan({c(18): {h: "x" for h in range(49)}, c(17): {h: "x" for h in range(19)}}, dt.datetime(2026, 11, 19, 21, tzinfo=UTC))
ok([(x[0].hour, x[1]) for x in p2] == [(18, "latest+complete+extended")] or [(x[0].hour, x[1]) for x in p2] == [(18, "latest+extended")], f"one run in several roles {[(x[0].hour, x[1]) for x in p2]}")
ok(conus.plan({c(19): {1: "x"}}, now) == [], "a cycle without f00 is not offered yet")

# 5. a run published earlier keeps growing: its hours are carried and new hours are added on the same grid
import tempfile  # noqa: E402
G = {"gridType": "lambert", "Nx": 1799, "Ny": 1059, "LaDInDegrees": 38.5, "LoVInDegrees": 262.5, "Latin1InDegrees": 38.5, "Latin2InDegrees": 38.5, "latitudeOfFirstGridPointInDegrees": 21.138123, "longitudeOfFirstGridPointInDegrees": 237.280472, "DxInMetres": 3000.0, "DyInMetres": 3000.0, "iScansNegatively": 0, "jScansPositively": 1, "jPointsAreConsecutive": 0, "radius": 6371229, "i0": 0, "j0": 0, "nx": 1799, "ny": 1059, "x0": -2697520.1425219304, "y0": -1587306.1525566624}   # the real HRRR CONUS grid keys (label points need the projection)
calls = {"carry": [], "proc": []}
real_carry, real_proc = conus.carry_hour, conus.process_hour
conus.carry_hour = lambda site, rel, f, fh, rec, out: calls["carry"].append(fh)
def fake_proc(cycle, fh, field, out, grid=None, rows_of=None):
    if grid is not None and grid != G: raise RuntimeError("grid changed")
    calls["proc"].append((field, fh)); return {"files": {}, "bytes": {"L0": 1, "L1": 1, "L2": 1}}, dict(G), ({"maxErrorCells": 0.0} if grid is None else None)
conus.process_hour = fake_proc
try:
    old = {"grid": {"projection": "lambert_conformal_conic (GRIB2 template 3.30)", **G}, "georef": {"maxErrorCells": 0.0},
           "fields": {"t2m": {"hours": {str(h): {"files": {}, "bytes": {"L0": 1, "L1": 1, "L2": 1}} for h in range(3)}}}}
    with tempfile.TemporaryDirectory() as d:
        man, p = conus.build_run(c(18), "latest", 18, False, {h: "x" for h in range(6)}, d, "data/conus/hrrr/x/conus-v1", old, "http://site", log=lambda *a: None)
    others = [f for f in conus.FIELDS if f != "t2m"]
    ok(calls["carry"] == [0, 1, 2] and sorted(h for f, h in calls["proc"] if f == "t2m") == [3, 4, 5]
       and all(sorted(h for f2, h in calls["proc"] if f2 == f) == list(range(6)) for f in others)
       and p == 3 + 6 * len(others) and man["hours"] == list(range(6)) and man["status"] == "in progress",
       f"carried {calls['carry']}, t2m processed {sorted(h for f, h in calls['proc'] if f == 't2m')}, {len(others)} new fields x 6 hours, status {man['status']}")
finally:
    conus.carry_hour, conus.process_hour = real_carry, real_proc

# 6. every product carries its source, derivation, attribution and redistribution terms
for f, spec in conus.FIELDS.items():
    ok(all(spec.get(k) for k in ("source", "derivation", "attribution", "terms")), f"{f}: provenance recorded")

# 7. new fields: precipitation-type precedence, snowfall differencing, f00 not applicable (synthetic arrays; no network)
import numpy as np  # noqa: E402
import fields as FX  # noqa: E402
def fake_get(flags):
    def get(url, rows, var, level, step, check, fh):
        k = {"Nx": 2, "Ny": 1}; return k, np.array([flags[var]], float), {"idx": var, "grib": {}}
    return get
real_get = FX._get
try:
    FX._get = fake_get({"CRAIN": [1, 1], "CSNOW": [1, 0], "CICEP": [0, 0], "CFRZR": [1, 0]})
    k, v, s = FX.read_ptype("u", [], 3, None)
    ok(v.tolist() == [[4, 1]], f"precipitation type: freezing rain wins over snow and rain; rain alone stays rain ({v.tolist()})")
    try: FX.read_ptype("u", [], 0, None); ok(False, "f00 type should be not applicable")
    except FX.NotApplicable: ok(True, "f00 precipitation type is not applicable (HRRR analysis has no type)")
    runs = {5: [0.10, 0.20], 6: [0.12, 0.19999]}   # metres of snow, run totals at f05 and f06
    FX._get = lambda url, rows, var, level, step, check, fh: ({"Nx": 2, "Ny": 1}, np.array([runs[fh]]), {"idx": var, "grib": {}})
    k, v, s = FX.read_asnow1h("u", [], 6, lambda fh: ("u", []))
    ok(np.allclose(v, [[0.02 * FX.IN_PER_M, 0.0]]) and s[0]["derivation"]["largestDecreaseClippedIn"] > 0, f"1-h snowfall = run-total difference; tiny decreases set to 0 ({v.round(3).tolist()})")
    runs[6] = [0.10, 0.10]
    try: FX.read_asnow1h("u", [], 6, lambda fh: ("u", [])); ok(False, "a large decrease should fail the hour")
    except RuntimeError: ok(True, "a run-total decrease larger than packing noise fails the hour instead of publishing")
finally:
    FX._get = real_get
ok(FX._step_acc(0, 24) == ["0-24 hour acc fcst", "0-1 day acc fcst"] and FX._step_acc(23, 24) == ["23-24 hour acc fcst"] and FX._step_acc(0, 48)[1] == "0-2 day acc fcst",
   "run totals at F24/F48 are found under the index's day wording (0-1 day, 0-2 day) as well as hours")
ok(FX.encode("apcp1h", np.array([[0.0123, np.nan]])).tolist() == [[12, -32768]], "precipitation stored in thousandths of an inch; NaN stays missing")


# 8. carrying a published hour: the previous build's local copy is used when its hash matches; otherwise the live site
import hashlib as _h, tempfile as _t  # noqa: E402
with _t.TemporaryDirectory() as prev:
    rel, name, good = "data/conus/hrrr/x/conus-v1", "t2m/f01/L0_0_0.bin.gz", b"tile-bytes"
    os.makedirs(os.path.join(prev, rel, "t2m/f01")); open(os.path.join(prev, rel, name), "wb").write(good)
    calls = []; real_http = conus.hrrr.http; conus.hrrr.http = lambda url, *a, **k: (calls.append(url), good)[1]
    try:
        conus.PREV = prev; conus.CARRY_STATS.update(local=0, http=0)
        b1 = conus.fetch_carry("http://site", rel, name, len(good), lambda x: _h.sha256(x).hexdigest()[:16] == _h.sha256(good).hexdigest()[:16])
        open(os.path.join(prev, rel, name), "wb").write(b"tile-BYTES")   # same size, different content
        b2 = conus.fetch_carry("http://site", rel, name, len(good), lambda x: _h.sha256(x).hexdigest()[:16] == _h.sha256(good).hexdigest()[:16])
        ok(b1 == good and b2 == good and conus.CARRY_STATS == {"local": 1, "http": 1} and calls == [f"http://site/{rel}/{name}"],
           f"carry: local copy used when its hash matches, live site when it does not ({conus.CARRY_STATS})")
    finally:
        conus.hrrr.http = real_http; conus.PREV = None


# 9. NBM: alternating row scanning is undone (stored rows 1, 3, ... run east to west), and 6-h PoP exists only for windows ending 00/06/12/18Z
from eccodes import codes_grib_new_from_samples, codes_set, codes_set_values, codes_get_message, codes_new_from_message, codes_release  # noqa: E402
import nbm, hrrr  # noqa: E402
def _synthetic(alt):
    g = codes_grib_new_from_samples("GRIB2"); codes_set(g, "Ni", 4); codes_set(g, "Nj", 3); codes_set(g, "alternativeRowScanning", alt)
    codes_set_values(g, np.arange(12, dtype=float)); g2 = codes_new_from_message(codes_get_message(g)); codes_release(g)
    try: return hrrr.grid_values(g2).tolist()
    finally: codes_release(g2)
ok(_synthetic(1) == [[0, 1, 2, 3], [7, 6, 5, 4], [8, 9, 10, 11]] and _synthetic(0) == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11]],
   "alternative row scanning: every second stored row is reversed into west-to-east order; ordinary grids are untouched")
_c = dt.datetime(2026, 11, 22, 13, tzinfo=dt.timezone.utc); _r = nbm.pop6h_reader(_c); _na = []
for _fh in (1, 4, 5, 11, 17, 6):
    try: _r("u", [], _fh, None)
    except FX.NotApplicable: _na.append(_fh)
    except Exception: pass   # applicable: it went on to look for the message in the (empty) index
ok(_na == [1, 4, 5, 6], f"NBM 6-h PoP from a 13Z run: F11 and F17 (windows ending 00Z, 06Z) applicable; F05 would start before the run, as in the live 13Z index ({_na})")
ok(set(nbm.FIELDS) == {"t2m", "pop1h", "pop6h"} and all(f["attribution"].startswith("NOAA/NWS National Blend") and "no copyright" in f["terms"] for f in nbm.FIELDS.values()),
   "NBM fields carry NBM attribution and terms")
# 10. NBM Day 3: hourly to F48 then every 3 h; 1-h PoP stops at F48; R2 pruning is per model and never touches a model
#     whose build is missing from this site
_hl = nbm.hour_list(72)
ok(len(_hl) == 56 and _hl[47] == 48 and _hl[48:] == [51, 54, 57, 60, 63, 66, 69, 72] and nbm.hour_list(24) == list(range(1, 25)), f"NBM hours through F72: {len(_hl)} (F01-F48 hourly, then F51-F72 3-hourly)")
try: nbm.read_pop1h("u", [], 51, None); _p1 = "no"
except FX.NotApplicable as e: _p1 = str(e)
ok("through F48" in _p1, f"NBM 1-h PoP after F48 is not applicable, with the reason ({_p1})")
import r2_sync, json as _j  # noqa: E402
with tempfile.TemporaryDirectory() as _site:
    def _put(rel, body=b"x"): os.makedirs(os.path.dirname(os.path.join(_site, rel)), exist_ok=True); open(os.path.join(_site, rel), "wb").write(body)
    _put("data/conus/index.json", _j.dumps({"runs": [{"path": "data/conus/hrrr/B/conus-v1-packed"}]}).encode()); _put("data/conus/hrrr/B/conus-v1-packed/t2m/f01.pack")
    _existing = {"data/conus/hrrr/A/conus-v1-packed/t2m/f01.pack": 1, "data/conus/nbm/OLD/conus-v1-packed/t2m/f01.pack": 1}
    _u, _d, _k = r2_sync.plan(_site, _existing)
    ok(_d == ["data/conus/hrrr/A/conus-v1-packed/t2m/f01.pack"], f"R2 sync without an NBM build: prunes the old HRRR run only, keeps NBM ({_d})")
    _put("data/conus/nbm/index.json", _j.dumps({"runs": [{"path": "data/conus/nbm/NEW/conus-v1-packed"}]}).encode()); _put("data/conus/nbm/NEW/conus-v1-packed/t2m/f01.pack")
    _u, _d, _k = r2_sync.plan(_site, _existing)
    ok(sorted(_d) == sorted(_existing) and any(u[0] == "data/conus/nbm/index.json" for u in _u), f"R2 sync with an NBM build: prunes the old run of each model and uploads the NBM index ({len(_d)} deleted)")
    # an unchanged manifest or index is not uploaded again (its MD5 equals the bucket ETag); a changed one is
    _ix = open(os.path.join(_site, "data/conus/nbm/index.json"), "rb").read(); import hashlib as _hl
    _u, _d, _k = r2_sync.plan(_site, {**_existing, "data/conus/nbm/index.json": (len(_ix), _hl.md5(_ix).hexdigest())})
    ok("data/conus/nbm/index.json" in _k and not any(u[0] == "data/conus/nbm/index.json" for u in _u), "R2 sync: an unchanged .json (same MD5 as the bucket ETag) is kept, not uploaded")
    _u, _d, _k = r2_sync.plan(_site, {**_existing, "data/conus/nbm/index.json": (len(_ix), "0" * 32)})
    ok(any(u[0] == "data/conus/nbm/index.json" for u in _u), "R2 sync: a changed .json is uploaded")
    # label points inside the pack: read back from [offset, length], or from the separate file of older hours
    _put("data/conus/hrrr/B/conus-v1-packed/t2m/f02.pack", b"TILES" + b"PTS")
    ok(conus.points_bytes(os.path.join(_site, "data/conus/hrrr/B/conus-v1-packed"), "t2m", 2, {"pack": {"file": "t2m/f02.pack", "points": [5, 3]}}) == b"PTS", "label points are read from inside the pack")
    _put("data/conus/hrrr/B/conus-v1-packed/t2m/p03.bin.gz", b"OLD")
    ok(conus.points_bytes(os.path.join(_site, "data/conus/hrrr/B/conus-v1-packed"), "t2m", 3, {"pack": {"file": "t2m/f03.pack"}, "points": [3, "x"]}) == b"OLD", "older hours read their separate label points file")
# 11. RRFS: its own reflectivity encoding and F00 type, its own labels and credit, run retention, and R2 pruning
import rrfs  # noqa: E402
ok(set(rrfs.FIELDS) == {"t2m", "d2m", "gust", "refc", "ptype", "apcp1h", "apcp_run", "asnow1h", "asnow_run"} and "frzr_run" not in rrfs.FIELDS,
   "RRFS fields: the HRRR set without freezing rain (not in the RRFS 3 km file)")
ok(all("HRRR" not in (sp["label"] + sp["source"] + sp.get("note", "") + sp["attribution"]) and sp["attribution"] == "NOAA/NCEP RRFS" and "RRFS" in sp["terms"] for sp in rrfs.FIELDS.values()),
   "RRFS fields name RRFS (not HRRR) in labels, sources, notes and credit")
ok(rrfs.FIELDS["refc"]["read"] is rrfs.read_refc and rrfs.FIELDS["ptype"]["read"] is rrfs.read_ptype and rrfs.FIELDS["t2m"]["read"] is FX.read_t2m, "RRFS reflectivity and type use RRFS readers; the rest the shared ones")
_c = lambda h, d=22: dt.datetime(2026, 11, d, h, tzinfo=UTC)
ok(rrfs.n_hours(_c(18)) == 48 and rrfs.n_hours(_c(21)) == 18 and rrfs.n_hours(_c(18), 84) == 84, "RRFS spans: 00/06/12/18Z to F48 (F84 available), 03/09/15/21Z to F18")
_k = rrfs.keep([(_c(21), 18, {}), (_c(15), 18, {}), (_c(12), 48, {})])
ok([c.hour for c, _, _ in _k] == [21, 15, 12], f"RRFS retention: two newest runs plus the newest long run ({[c.hour for c, _, _ in _k]})")
ok([c.hour for c, _, _ in rrfs.keep([(_c(18), 48, {}), (_c(15), 18, {}), (_c(12), 48, {})])] == [18, 15], "RRFS retention: no extra run when a long run is among the two newest")
with tempfile.TemporaryDirectory() as _site:
    def _put(rel, body=b"x"): os.makedirs(os.path.dirname(os.path.join(_site, rel)), exist_ok=True); open(os.path.join(_site, rel), "wb").write(body)
    _put("data/conus/index.json", _j.dumps({"runs": []}).encode())
    _ex = {"data/conus/rrfs/OLD/conus-v1-packed/t2m/f01.pack": 1}
    ok(r2_sync.plan(_site, _ex)[1] == [], "R2 sync without an RRFS build keeps the RRFS data in the bucket")
    _put("data/conus/rrfs/index.json", _j.dumps({"runs": [{"path": "data/conus/rrfs/NEW/conus-v1-packed"}]}).encode())
    ok(r2_sync.plan(_site, _ex)[1] == list(_ex), "R2 sync with an RRFS build prunes RRFS runs no longer retained")
# 12. ECMWF IFS open data: 3-hourly steps to F120, two runs kept (newest may be partial), ptype codes, the lat-lon crop
import ecmwf  # noqa: E402
ok(ecmwf.STEPS == list(range(0, 121, 3)) and len(ecmwf.STEPS) == 41 and ecmwf.CYCLES == (0, 12), "ECMWF steps: F00-F120 every 3 h (41), 00Z and 12Z runs")
_now = dt.datetime(2026, 11, 24, 9, tzinfo=UTC)
_av = {dt.datetime(2026, 11, 24, 0, tzinfo=UTC): {h: "x" for h in range(0, 49, 3)}, dt.datetime(2026, 11, 23, 12, tzinfo=UTC): {h: "x" for h in ecmwf.STEPS},
       dt.datetime(2026, 11, 23, 0, tzinfo=UTC): {h: "x" for h in ecmwf.STEPS}}
_p = ecmwf.plan(_av, _now)
ok([(c.day, c.hour, r, d) for c, r, _, d, _ in _p] == [(24, 0, "newest", False), (23, 12, "previous", True)], f"ECMWF retention: the two newest runs, the newest published while partial ({[(c.hour, r, d) for c, r, _, d, _ in _p]})")
ok(ecmwf.plan(_av, dt.datetime(2026, 11, 24, 13, tzinfo=UTC))[0][3] is True, "ECMWF: a run still missing steps 12 h after its cycle time is final (with gaps)")
_codes = np.array([0, 1, 3, 5, 6, 7, 8, 11, 12, 2, 4, 9, 10, 1.5, np.nan, 255])
_m = ecmwf.map_ptype(_codes)
ok(_m[:9].tolist() == [0, 1, 4, 2, 2, 2, 3, 1, 4] and np.isnan(_m[9:]).all(),
   f"ECMWF ptype: rain/drizzle -> rain, snow/wet snow/mix -> snow, ice pellets, freezing rain/drizzle -> freezing rain; unknown and non-integer codes missing, never rain ({_m.tolist()})")
ok(all(FX.PTYPE_CODES[c] for c in ecmwf.PTYPE_MAP.values()) and set(ecmwf.PTYPE_SOURCE) == set(ecmwf.PTYPE_MAP), "every mapped ECMWF code is documented and lands on a viewer category")
_rows = [{"var": "10fg", "level": "sfc", "step": "6"}, {"var": "10fg3", "level": "sfc", "step": "93"}]
ok(ecmwf.gust_param(_rows, 6) == ("10fg", 1) and ecmwf.gust_param(_rows, 93) == ("10fg3", 3), "ECMWF gust: 1-h maximum (10fg) through F90, 3-h maximum (10fg3) after")
for _r, _fh in ((ecmwf.read_gust, 0), (ecmwf.read_apcp_run, 0), (ecmwf.read_asnow_run, 0)):
    try: _r("u", [], _fh, None); ok(False, "F00 window should be not applicable")
    except FX.NotApplicable: ok(True, f"ECMWF {_r.__name__} at F00 is not applicable (0-0 h window)")
ok(set(ecmwf.FIELDS) == {"t2m", "d2m", "gust", "ptype", "apcp_run", "asnow_run"} and all(sp["attribution"] == ecmwf.CREDIT and "CC BY 4.0" in sp["terms"]
   and all(sp.get(k) for k in ("source", "derivation")) for sp in ecmwf.FIELDS.values()), "ECMWF fields carry the ECMWF CC BY 4.0 credit, terms, source and derivation")
ok("10:1" in ecmwf.FIELDS["asnow_run"]["label"] and "water equivalent" in ecmwf.FIELDS["asnow_run"]["derivation"] and ecmwf.FIELDS["asnow_run"]["snowRatio"] == 10,
   "ECMWF snowfall states its 10:1 ratio and that ECMWF provides water equivalent only")
_real = ecmwf.hrrr.http
_real_e = ecmwf.http
try:
    _real_e = ecmwf.http; ecmwf.http = lambda url, *a, **k: (b'{"param": "2t", "levtype": "sfc", "step": "9", "_offset": 100, "_length": 50}\n'
                                             b'{"param": "t", "levtype": "pl", "levelist": "850", "step": "9", "_offset": 150, "_length": 7}\n') if url.endswith("-9h-oper-fc.index") else 1 / 0
    _ix = ecmwf.index_rows(ecmwf.url_of(dt.datetime(2026, 11, 23, 12, tzinfo=UTC), 9))
finally:
    ecmwf.hrrr.http = _real; ecmwf.http = _real_e
ok([(r["var"], r["level"], r["step"], r["start"], r["end"]) for r in _ix] == [("2t", "sfc", "9", 100, 149), ("t", "pl:850", "9", 150, 156)], f"ECMWF .index JSON lines -> byte ranges ({_ix})")
# lat-lon grid: the ECMWF global 0.25 degree keys -> the CONUS crop, cells <-> lat/lon, the crop of a full array, label points
_K = {"gridType": "regular_ll", "Ni": 1440, "Nj": 721, "latitudeOfFirstGridPointInDegrees": 90.0, "longitudeOfFirstGridPointInDegrees": 180.0, "latitudeOfLastGridPointInDegrees": -90.0,
      "longitudeOfLastGridPointInDegrees": 179.75, "iDirectionIncrementInDegrees": 0.25, "jDirectionIncrementInDegrees": 0.25, "iScansNegatively": 0, "jScansPositively": 0, "jPointsAreConsecutive": 0}
_g = conus.ll_grid(_K, ecmwf.CROP)
ok((_g["i0"], _g["j0"], _g["rowStep"], _g["nx"], _g["ny"], _g["lat0"], _g["lon0"]) == (180, 280, -1, 301, 141, 20.0, -135.0), f"ECMWF crop: columns 180-480, GRIB rows 280 up to 140, 301 x 141 cells from 20N 135W ({_g})")
_fi, _fj = conus.to_cell(_g, *conus.from_cell(_g, np.arange(301)[None, :], np.arange(141)[:, None]))
ok(np.abs(_fi - np.arange(301)[None, :]).max() < 1e-9 and np.abs(_fj - np.arange(141)[:, None]).max() < 1e-9, "lat-lon cells round trip (cell -> lat/lon -> cell) everywhere")
_lat = np.repeat((90 - 0.25 * np.arange(721))[:, None], 1440, axis=1); _lon = np.repeat((180 + 0.25 * np.arange(1440))[None, :], 721, axis=0)
_cl, _co = conus.crop(_lat, _g), ((conus.crop(_lon, _g) + 180) % 360) - 180
ok(_cl.shape == (141, 301) and _cl[0, 0] == 20 and _cl[-1, 0] == 55 and _co[0, 0] == -135 and _co[0, -1] == -60, "the crop is native cells, rows south to north, columns west to east")
_ij, _pd = conus.label_points(_g)
ok(_pd["lattice"]["every"] == conus.LATTICE_LL and len(_ij) == len(_pd["places"]) + _pd["lattice"]["nx"] * _pd["lattice"]["ny"] and len(_pd["places"]) > 50
   and all(_cl[j, i] == round(lat * 4) / 4 and _co[j, i] == round(lon * 4) / 4 for (_n, lat, lon, _pop, i, j) in _pd["places"]),
   f"lat-lon label points: {len(_pd['places'])} places on their nearest 0.25 degree cell, plus an every-{conus.LATTICE_LL} lattice")
_bad = {**_K, "iScansNegatively": 1}
try: conus.ll_grid(_bad, ecmwf.CROP); ok(False, "unsupported scanning accepted")
except RuntimeError: ok(True, "a lat-lon grid scanning east to west is refused, not mis-placed")
try: conus.ll_grid(_K, {"lat": [20.1, 55.0], "lon": [-135.0, -60.0]}); ok(False, "off-grid crop accepted")
except RuntimeError: ok(True, "a crop bound between grid points is refused (crop, never resample)")
# a real regular_ll GRIB message (eccodes sample): georef_ll checks every cell against GRIB's own lat/lon and the checkpoints
from eccodes import codes_grib_new_from_samples as _new, codes_set as _set, codes_set_values as _setv, codes_get_message as _msg, codes_release as _rel  # noqa: E402
_s = _new("regular_ll_sfc_grib2")
for _k2, _v2 in (("Ni", 1440), ("Nj", 721), ("latitudeOfFirstGridPointInDegrees", 90.0), ("longitudeOfFirstGridPointInDegrees", 180.0), ("latitudeOfLastGridPointInDegrees", -90.0),
                 ("longitudeOfLastGridPointInDegrees", 179.75), ("iDirectionIncrementInDegrees", 0.25), ("jDirectionIncrementInDegrees", 0.25), ("jScansPositively", 0)): _set(_s, _k2, _v2)
_setv(_s, np.arange(1440 * 721, dtype=float) % 1000); _buf = _msg(_s); _rel(_s)
_k, _v = FX.decode(_buf); _g2 = conus.ll_grid(_k, ecmwf.CROP)
_geo = conus.georef(_buf, _g2)
ok(_g2 == _g and _geo["maxErrorCells"] < 1e-6 and len(_geo["checkpoints"]) == len(conus.CHECKPOINTS) and _geo["corners"]["sw"] == [20.0, -135.0],
   f"a real lat-lon GRIB: decoded grid = the crop, every cell centre = GRIB lat/lon (error {_geo['maxErrorCells']}), eccodes nearest points = our cells")
ok(all(conus.crop(_v, _g2)[c["cell"][1], c["cell"][0]] == c["value"] for c in _geo["checkpoints"]), "checkpoint values read through the crop equal eccodes' nearest-point values")
# R2: ECMWF is in the per-model prune list
with tempfile.TemporaryDirectory() as _site:
    def _put(rel, body=b"x"): os.makedirs(os.path.dirname(os.path.join(_site, rel)), exist_ok=True); open(os.path.join(_site, rel), "wb").write(body)
    _put("data/conus/index.json", _j.dumps({"runs": []}).encode())
    _ex = {"data/conus/ecmwf/OLD/conus-v1-packed/t2m/f03.pack": 1}
    ok("ecmwf" in r2_sync.MODELS and r2_sync.plan(_site, _ex)[1] == [], "R2 sync without an ECMWF build keeps the ECMWF data in the bucket")
    _put("data/conus/ecmwf/index.json", _j.dumps({"runs": [{"path": "data/conus/ecmwf/NEW/conus-v1-packed"}]}).encode())
    ok(r2_sync.plan(_site, _ex)[1] == list(_ex), "R2 sync with an ECMWF build prunes ECMWF runs no longer retained")
print(f"ok - {n} CONUS packager checks passed (with carry)")
