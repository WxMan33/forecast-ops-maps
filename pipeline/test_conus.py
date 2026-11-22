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
print(f"ok - {n} CONUS packager checks passed (with carry)")
