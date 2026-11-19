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
G = {"gridType": "lambert", "Nx": 1799, "Ny": 1059, "DxInMetres": 3000.0, "i0": 0, "j0": 0, "nx": 1799, "ny": 1059, "x0": -2.7e6, "y0": -1.6e6}
calls = {"carry": [], "proc": []}
real_carry, real_proc = conus.carry_hour, conus.process_hour
conus.carry_hour = lambda site, rel, f, fh, rec, out: calls["carry"].append(fh)
def fake_proc(cycle, fh, field, out, grid=None):
    if grid is not None and grid != G: raise RuntimeError("grid changed")
    calls["proc"].append(fh); return {"files": {}, "bytes": {"L0": 1, "L1": 1, "L2": 1}}, dict(G), ({"maxErrorCells": 0.0} if grid is None else None)
conus.process_hour = fake_proc
try:
    old = {"grid": {"projection": "lambert_conformal_conic (GRIB2 template 3.30)", **G}, "georef": {"maxErrorCells": 0.0},
           "fields": {"t2m": {"hours": {str(h): {"files": {}, "bytes": {"L0": 1, "L1": 1, "L2": 1}} for h in range(3)}}}}
    with tempfile.TemporaryDirectory() as d:
        man, p = conus.build_run(c(18), "latest", 18, False, {h: "x" for h in range(6)}, d, "data/conus/hrrr/x/conus-v1", old, "http://site", log=lambda *a: None)
    ok(calls["carry"] == [0, 1, 2] and sorted(calls["proc"]) == [3, 4, 5] and p == 3 and man["hours"] == list(range(6)) and man["status"] == "in progress",
       f"carried {calls['carry']}, processed {sorted(calls['proc'])}, status {man['status']}")
finally:
    conus.carry_hour, conus.process_hour = real_carry, real_proc

# 6. every product carries its source, derivation, attribution and redistribution terms
for f, spec in conus.FIELDS.items():
    ok(all(spec.get(k) for k in ("source", "derivation", "attribution", "terms")), f"{f}: provenance recorded")

print(f"ok - {n} CONUS packager checks passed")
