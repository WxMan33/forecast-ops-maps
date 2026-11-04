"""Self-test for the HRRR packager (synthetic inputs only; no network). Run: python pipeline/test_hrrr.py"""
import datetime as dt, math, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
import hrrr  # noqa: E402

n = 0
def ok(c, m):
    global n
    assert c, m; n += 1

U = hrrr.UTC
now = dt.datetime(2026, 11, 4, 19, 30, tzinfo=U)
full = lambda h: set(range(0, h + 1))
# retention: newest usable run + the two newest complete 48-h runs; incomplete runs wait
av = {dt.datetime(2026, 11, 4, 18, tzinfo=U): set(range(0, 31)), dt.datetime(2026, 11, 4, 17, tzinfo=U): full(18),
      dt.datetime(2026, 11, 4, 16, tzinfo=U): full(18), dt.datetime(2026, 11, 4, 12, tzinfo=U): full(48),
      dt.datetime(2026, 11, 4, 6, tzinfo=U): full(48), dt.datetime(2026, 11, 4, 0, tzinfo=U): full(48)}
p = hrrr.plan(av, now)
ok([(c.hour, r, h) for c, r, h in p] == [(17, "latest", 18), (12, "extended", 48), (6, "extended", 48)], f"retention plan {p}")
# an extended run that is complete and newest is both latest and extended
p2 = hrrr.plan({dt.datetime(2026, 11, 4, 18, tzinfo=U): full(48), dt.datetime(2026, 11, 4, 12, tzinfo=U): full(48)}, now)
ok([(c.hour, r) for c, r, _ in p2] == [(18, "latest+extended"), (12, "extended")], f"extended newest {p2}")
# a run with a permanently missing hour is published (missing marked) only after the give-up delay
gap = {dt.datetime(2026, 11, 4, 13, tzinfo=U): full(18) - {7}}
ok(hrrr.plan(gap, dt.datetime(2026, 11, 4, 16, tzinfo=U)) == [], "a recent incomplete run waits")
ok(len(hrrr.plan(gap, dt.datetime(2026, 11, 4, 21, tzinfo=U))) == 1, "an old incomplete run is published with its missing hour")
ok(hrrr.plan({}, now) == [], "nothing available: nothing planned")

# classification from GRIB metadata only
base = {"typeOfLevel": "surface", "level": 0, "units": "kg m**-2", "stepUnits": 1}
ok(hrrr.classify({**base, "shortName": "tp", "stepType": "accum", "startStep": 5, "endStep": 6}, 6) == "apcp_hour", "(N-1)-N accumulation is the hourly field")
ok(hrrr.classify({**base, "shortName": "tp", "stepType": "accum", "startStep": 0, "endStep": 6}, 6) == "apcp_run", "0-N accumulation is the run total")
ok(hrrr.classify({**base, "shortName": "tp", "stepType": "accum", "startStep": 0, "endStep": 1}, 1) == "apcp_run+hour", "0-1 h is both")
ok(hrrr.classify({**base, "shortName": "tp", "stepType": "accum", "startStep": 3, "endStep": 6}, 6) is None, "3-h accumulations are not mislabelled")
ok(hrrr.classify({**base, "shortName": "tp", "stepType": "accum", "startStep": 5, "endStep": 6, "units": "m"}, 6) is None, "unexpected units are rejected")
ok(hrrr.classify({**base, "shortName": "tp", "stepType": "accum", "startStep": 4, "endStep": 5}, 6) is None, "an accumulation for another hour is rejected")
ok(hrrr.classify({"shortName": "2t", "typeOfLevel": "heightAboveGround", "level": 2, "stepType": "instant", "units": "K"}, 3) == "t2m", "2-m temperature")
ok(hrrr.classify({"shortName": "2t", "typeOfLevel": "heightAboveGround", "level": 2, "stepType": "max", "units": "K"}, 3) is None, "a max temperature is not the instantaneous field")
ok(hrrr.classify({"shortName": "refc", "stepType": "instant"}, 3) == "refc", "composite reflectivity")

# encoding: missing stays missing; units converted
e = hrrr.encode("t2m", np.array([[273.15, np.nan]]))
ok(e[0, 0] == 320 and e[0, 1] == hrrr.MISS16, "0 C = 32.0 F packed as 320; NaN -> sentinel")
e = hrrr.encode("apcp_hour", np.array([[0.0, 1.234, np.nan, -0.5]]))
ok(list(e[0]) == [0, 123, hrrr.MISSU16, 0], "precip packed in 0.01 mm; NaN -> sentinel; never negative")

# projection: HRRR's first grid point maps to cell (0, 0); a 3 km step moves one cell
proj = {"Latin1InDegrees": 38.5, "Latin2InDegrees": 38.5, "LaDInDegrees": 38.5, "LoVInDegrees": 262.5, "radius": 6371229,
        "latitudeOfFirstGridPointInDegrees": 21.138123, "longitudeOfFirstGridPointInDegrees": 237.280472, "DxInMetres": 3000.0, "DyInMetres": 3000.0}
fwd = hrrr.lcc(proj)
x0, y0 = fwd(proj["latitudeOfFirstGridPointInDegrees"], proj["longitudeOfFirstGridPointInDegrees"])
win = hrrr.crop_window(proj)
ok(win["nx"] > 100 and win["ny"] > 100 and win["i0"] > 0 and win["j0"] > 0, f"crop window {win}")
xc, yc = fwd(39.04456, -84.67229)
ci, cj = (xc - x0) / 3000 - win["i0"], (yc - y0) / 3000 - win["j0"]
ok(round(ci) == 63 and round(cj) == 61, f"CVG falls in crop cell 63,61 (as eccodes found on real data): {ci:.2f},{cj:.2f}")
print(f"ok - {n} HRRR packager checks passed")
