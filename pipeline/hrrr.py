#!/usr/bin/env python3
"""HRRR Tri-State map packages for Forecast Ops (hrrr-maps-v1).

Reads NOAA HRRR CONUS 2-D surface files (wrfsfcfNN.grib2) from the NOAA Open Data Dissemination
bucket on AWS, fetching only the needed GRIB messages by byte range (from each file's .idx). Every
message is identified from its own GRIB metadata, never from the index text alone:
  2-m temperature      shortName 2t, heightAboveGround 2 m, stepType instant          (K -> degF)
  composite refl.      shortName refc, entire atmosphere, stepType instant            (dB = dBZ)
  precipitation        shortName tp, surface, stepType accum, units kg m**-2 (= mm); the
                       accumulation interval [startStep, endStep] is read from the message:
                         apcp_hour   interval (N-1, N): the last hour
                         apcp_run    interval (0, N):   since the run started
The native Lambert conformal grid is preserved: the package stores the GRIB projection parameters
and a crop window of whole grid cells (no resampling). The projection is checked against the GRIB
latitudes/longitudes of every cropped cell before anything is published.

Output: one immutable package per run under data/hrrr/<YYYYMMDDHH>/ (manifest.json + one gzip
file per field holding all forecast hours), and data/index.json listing the retained runs. The
caller assembles the whole site and deploys it in one step, so a failed run never replaces a
working one. Missing hours/fields stay missing (sentinel), never zero.
"""
import argparse, concurrent.futures as cf, datetime as dt, gzip, hashlib, json, math, os, re, sys, time
import urllib.request, urllib.error
import numpy as np

VERSION = "hrrr-maps-v1"
BUCKET = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
UA = {"User-Agent": "forecast-ops-maps HRRR Tri-State packager (github.com/WxMan33/forecast-ops-maps)"}
BOX = {"lat": [37.6, 40.7], "lon": [-86.6, -82.4]}   # Cincinnati Tri-State and surroundings
EXTENDED = {0, 6, 12, 18}                            # 48-h cycles; the others run 18 h
UTC = dt.timezone.utc
# Placement checkpoints: eccodes' own nearest-gridpoint search on the FULL CONUS message is compared
# with the package's crop at these points; the viewer's tests repeat the comparison in JavaScript.
CHECKPOINTS = [("CVG airport", 39.04456, -84.67229), ("Downtown Cincinnati", 39.1031, -84.5120), ("Dayton (DAY)", 39.9024, -84.2194),
               ("Louisville (SDF)", 38.1744, -85.7360), ("Lexington (LEX)", 38.0365, -84.6059), ("Columbus (CMH)", 39.9980, -82.8919),
               ("Huntington (HTS)", 38.3667, -82.5580), ("SW corner area", 37.75, -86.45), ("NE corner area", 40.6, -82.5)]
MISS16 = -32768
MISSU16 = 65535
FIELDS = {
    "t2m":       {"label": "2-m temperature", "units": "degF", "scale": 0.1, "dtype": "int16", "kind": "instant"},
    "refc":      {"label": "Simulated composite reflectivity", "units": "dBZ", "scale": 0.1, "dtype": "int16", "kind": "instant",
                  "note": "model-simulated, not radar"},
    "apcp_hour": {"label": "Precipitation, previous hour", "units": "mm", "scale": 0.01, "dtype": "uint16", "kind": "accum"},
    "apcp_run":  {"label": "Precipitation since run start", "units": "mm", "scale": 0.01, "dtype": "uint16", "kind": "accum"},
}


def http(url, rng=None, tries=4, timeout=60):
    h = dict(UA)
    if rng: h["Range"] = f"bytes={rng[0]}-{rng[1]}" if rng[1] is not None else f"bytes={rng[0]}-"
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404): raise
            if k == tries - 1: raise
        except Exception:
            if k == tries - 1: raise
        time.sleep(2 * (k + 1))


def list_keys(prefix):
    keys, token = [], None
    while True:
        q = f"{BUCKET}/?list-type=2&prefix={prefix}" + (f"&continuation-token={urllib.parse.quote(token)}" if token else "")
        x = http(q).decode()
        keys += re.findall(r"<Key>([^<]+)</Key>", x)
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", x)
        if not m: return keys
        token = m.group(1)


import urllib.parse  # noqa: E402


def available(now):
    """{cycle datetime: set(forecast hours with an .idx)} for today and yesterday (UTC)."""
    out = {}
    for d in (now.date(), now.date() - dt.timedelta(days=1)):
        for k in list_keys(f"hrrr.{d:%Y%m%d}/conus/hrrr.t"):
            m = re.search(r"hrrr\.(\d{8})/conus/hrrr\.t(\d{2})z\.wrfsfcf(\d{2})\.grib2\.idx$", k)
            if m:
                c = dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H").replace(tzinfo=UTC)
                out.setdefault(c, set()).add(int(m.group(3)))
    return out


def plan(avail, now, give_up_h=4.0):
    """Which runs to keep: the newest usable run of any cycle, plus the two newest 48-h runs.
    A run is usable when every forecast hour is present, or when it is older than give_up_h past
    its expected completion and some hours exist (those missing are published as missing)."""
    def usable(c, hours):
        n = 48 if c.hour in EXTENDED else 18
        full = set(range(0, n + 1))
        if full <= hours: return True, n
        age_h = (now - c).total_seconds() / 3600
        return (age_h > (2.0 if n == 18 else 2.5) + give_up_h and len(hours & full) > 0), n
    ok = sorted([(c, n) for c, h in avail.items() for u, n in [usable(c, h)] if u], reverse=True)
    if not ok: return []
    keep = {ok[0][0]: ("latest", ok[0][1])}
    ext = [x for x in ok if x[0].hour in EXTENDED][:2]
    for c, n in ext: keep[c] = ("latest+extended" if c in keep else "extended", n)
    return sorted([(c, role, n) for c, (role, n) in keep.items()], reverse=True)


def idx_rows(url):
    rows = [l.split(":") for l in http(url + ".idx").decode().splitlines() if l.strip()]
    out = []
    for i, r in enumerate(rows):
        s = int(r[1]); e = int(rows[i + 1][1]) - 1 if i + 1 < len(rows) else None
        out.append({"n": int(r[0]), "start": s, "end": e, "var": r[3], "level": r[4], "step": r[5]})
    return out


def candidates(rows):
    """Index entries that may hold our fields; each is confirmed from GRIB metadata after download."""
    return [r for r in rows if (r["var"] == "TMP" and r["level"] == "2 m above ground") or r["var"] == "REFC"
            or (r["var"] == "APCP" and r["level"] == "surface")]


def decode(buf):
    from eccodes import codes_new_from_message, codes_get, codes_get_values, codes_release
    g = codes_new_from_message(buf)
    try:
        k = {n: codes_get(g, n) for n in ("shortName", "typeOfLevel", "level", "stepType", "startStep", "endStep", "stepUnits", "units",
                                          "dataDate", "dataTime", "gridType", "Nx", "Ny", "LaDInDegrees", "LoVInDegrees", "Latin1InDegrees",
                                          "Latin2InDegrees", "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
                                          "DxInMetres", "DyInMetres", "iScansNegatively", "jScansPositively", "jPointsAreConsecutive", "radius")}
        v = codes_get_values(g).reshape(k["Ny"], k["Nx"])
        return k, v
    finally:
        codes_release(g)


def classify(k, fh):
    """Map a decoded message to one of our fields, from GRIB metadata only; None if it is not one."""
    if k["shortName"] == "2t" and k["typeOfLevel"] == "heightAboveGround" and k["level"] == 2 and k["stepType"] == "instant" and k["units"] == "K":
        return "t2m"
    if k["shortName"] == "refc" and k["stepType"] == "instant":
        return "refc"
    if k["shortName"] == "tp" and k["stepType"] == "accum" and k["typeOfLevel"] == "surface" and k["units"] == "kg m**-2" and k["stepUnits"] == 1 and k["endStep"] == fh:
        if k["startStep"] == 0 and k["endStep"] == fh:
            return "apcp_run" if fh != 1 else "apcp_run+hour"   # 0-1 h is both the run total and the last hour
        if k["startStep"] == fh - 1:
            return "apcp_hour"
    return None


GRID_KEYS = ("gridType", "Nx", "Ny", "LaDInDegrees", "LoVInDegrees", "Latin1InDegrees", "Latin2InDegrees", "latitudeOfFirstGridPointInDegrees",
             "longitudeOfFirstGridPointInDegrees", "DxInMetres", "DyInMetres", "iScansNegatively", "jScansPositively", "jPointsAreConsecutive", "radius")


def lcc(proj):
    """Spherical Lambert conformal conic (GRIB2 template 3.30, HRRR: tangent at 38.5N)."""
    R = proj["radius"]; d = math.radians
    p1, p2, p0, l0 = d(proj["Latin1InDegrees"]), d(proj["Latin2InDegrees"]), d(proj["LaDInDegrees"]), d(((proj["LoVInDegrees"] + 180) % 360) - 180)
    n = math.sin(p1) if abs(p1 - p2) < 1e-12 else math.log(math.cos(p1) / math.cos(p2)) / math.log(math.tan(math.pi / 4 + p2 / 2) / math.tan(math.pi / 4 + p1 / 2))
    F = math.cos(p1) * math.tan(math.pi / 4 + p1 / 2) ** n / n
    rho0 = R * F / math.tan(math.pi / 4 + p0 / 2) ** n
    def fwd(lat, lon):
        lat, lon = np.radians(lat), np.radians(((np.asarray(lon) + 180) % 360) - 180)
        rho = R * F / np.tan(np.pi / 4 + lat / 2) ** n
        th = n * (((lon - l0) + np.pi) % (2 * np.pi) - np.pi)
        return rho * np.sin(th), rho0 - rho * np.cos(th)
    return fwd


def crop_window(proj):
    """Grid index window (i0, j0, nx, ny) covering BOX, plus a georeference check against GRIB lat/lon."""
    fwd = lcc(proj)
    x0, y0 = fwd(proj["latitudeOfFirstGridPointInDegrees"], proj["longitudeOfFirstGridPointInDegrees"])
    xs, ys = [], []
    for la in np.linspace(*BOX["lat"], 40):
        for lo in np.linspace(*BOX["lon"], 40):
            x, y = fwd(la, lo); xs.append((x - x0) / proj["DxInMetres"]); ys.append((y - y0) / proj["DyInMetres"])
    i0, i1 = int(math.floor(min(xs))), int(math.ceil(max(xs))); j0, j1 = int(math.floor(min(ys))), int(math.ceil(max(ys)))
    return {"i0": i0, "j0": j0, "nx": i1 - i0 + 1, "ny": j1 - j0 + 1, "x0": float(x0), "y0": float(y0)}


def check_georef(buf, proj, win):
    """Projected cell centres must reproduce GRIB's own latitudes/longitudes for every cropped cell."""
    from eccodes import codes_new_from_message, codes_get_array, codes_release
    g = codes_new_from_message(buf)
    try:
        lat = codes_get_array(g, "latitudes").reshape(proj["Ny"], proj["Nx"]); lon = codes_get_array(g, "longitudes").reshape(proj["Ny"], proj["Nx"])
    finally:
        codes_release(g)
    s = (slice(win["j0"], win["j0"] + win["ny"]), slice(win["i0"], win["i0"] + win["nx"]))
    la, lo = lat[s], ((lon[s] + 180) % 360) - 180
    x, y = lcc(proj)(la, lo)
    ii = (x - win["x0"]) / proj["DxInMetres"] - win["i0"]; jj = (y - win["y0"]) / proj["DyInMetres"] - win["j0"]
    ei = np.abs(ii - np.arange(win["nx"])[None, :]); ej = np.abs(jj - np.arange(win["ny"])[:, None])
    err = float(max(ei.max(), ej.max()))
    if err > 0.01: raise RuntimeError(f"georeference check failed: projected cell centres differ from GRIB lat/lon by {err:.4f} cells")
    corners = {"sw": [float(la[0, 0]), float(lo[0, 0])], "se": [float(la[0, -1]), float(lo[0, -1])], "nw": [float(la[-1, 0]), float(lo[-1, 0])], "ne": [float(la[-1, -1]), float(lo[-1, -1])]}
    return {"maxErrorCells": round(err, 6), "corners": corners, "lat": la.astype(np.float32), "lon": lo.astype(np.float32)}


def checkpoints(buf, win):
    """Independent placement check: eccodes nearest grid point on the full message vs our crop cell."""
    from eccodes import codes_new_from_message, codes_grib_find_nearest, codes_get_values, codes_get, codes_release
    g = codes_new_from_message(buf)
    try:
        full = codes_get_values(g).reshape(codes_get(g, "Ny"), codes_get(g, "Nx")); out = []
        for name, la, lo in CHECKPOINTS:
            nb = codes_grib_find_nearest(g, la, lo)[0]
            j, i = divmod(int(nb["index"]), codes_get(g, "Nx"))
            ci, cj = i - win["i0"], j - win["j0"]
            if not (0 <= ci < win["nx"] and 0 <= cj < win["ny"]): raise RuntimeError(f"checkpoint {name} outside the crop")
            if abs(float(full[j, i]) - float(nb["value"])) > 1e-6: raise RuntimeError(f"checkpoint {name}: index/value mismatch")
            out.append({"name": name, "lat": la, "lon": lo, "cell": [ci, cj], "gribLat": round(float(nb["lat"]), 5), "gribLon": round(((float(nb["lon"]) + 180) % 360) - 180, 5),
                        "distanceKm": round(float(nb["distance"]), 3), "value": float(nb["value"]), "valueUnits": codes_get(g, "units")})
        return out
    finally:
        codes_release(g)


def encode(field, arr):
    f = FIELDS[field]
    a = np.asarray(arr, dtype=np.float64)
    if field == "t2m": a = (a - 273.15) * 9 / 5 + 32
    q = np.round(a / f["scale"])
    if f["dtype"] == "int16":
        out = np.where(np.isfinite(q), np.clip(q, -32767, 32767), MISS16).astype("<i2")
    else:
        out = np.where(np.isfinite(q), np.clip(q, 0, 65534), MISSU16).astype("<u2")
    return out


def process_run(cycle, nhours, out_dir, workers=6, log=print):
    base = f"{BUCKET}/hrrr.{cycle:%Y%m%d}/conus/hrrr.t{cycle:%H}z.wrfsfcf%02d.grib2"
    hours = list(range(0, nhours + 1))
    proj = win = geo = None; checks = None
    data = {f: [None] * len(hours) for f in FIELDS}
    meta = {f: [None] * len(hours) for f in FIELDS}
    sources, grib_bytes = [], 0

    def fetch_hour(fh):
        url = base % fh
        try: rows = candidates(idx_rows(url))
        except Exception as e: return fh, url, None, f"index unavailable ({e})"
        got = []
        for r in rows:
            buf = http(url, (r["start"], r["end"]))
            got.append((r, buf))
        return fh, url, got, None

    with cf.ThreadPoolExecutor(workers) as ex:
        results = sorted(ex.map(fetch_hour, hours), key=lambda x: x[0])
    for fh, url, got, err in results:
        h = hours.index(fh)
        if err:
            sources.append({"fh": fh, "url": url, "error": err}); continue
        for r, buf in got:
            k, v = decode(buf)
            grid = {g: k[g] for g in GRID_KEYS}
            if proj is None:
                proj = grid; win = crop_window(proj); geo = check_georef(buf, proj, win)
            elif grid != proj:
                raise RuntimeError(f"grid changed within run at f{fh:02d}")
            run = dt.datetime.strptime(f"{k['dataDate']}{int(k['dataTime']):04d}", "%Y%m%d%H%M").replace(tzinfo=UTC)
            if run != cycle: raise RuntimeError(f"f{fh:02d} message is from run {run:%Y%m%d%H}, not {cycle:%Y%m%d%H}")
            c = classify(k, fh)
            if c == "t2m" and checks is None: checks = {"fh": fh, "field": "t2m", "points": checkpoints(buf, win)}
            sha = hashlib.sha256(buf).hexdigest()
            sources.append({"fh": fh, "url": url, "range": [r["start"], r["end"]], "bytes": len(buf), "sha256": sha, "idx": f'{r["var"]}:{r["level"]}:{r["step"]}', "field": c,
                            "grib": {"shortName": k["shortName"], "stepType": k["stepType"], "startStep": k["startStep"], "endStep": k["endStep"], "units": k["units"]}})
            grib_bytes += len(buf)
            if not c: continue
            sub = v[win["j0"]:win["j0"] + win["ny"], win["i0"]:win["i0"] + win["nx"]]
            for f in (["apcp_run", "apcp_hour"] if c == "apcp_run+hour" else [c]):
                data[f][h] = sub; meta[f][h] = {"startStep": k["startStep"], "endStep": k["endStep"]}
    if proj is None: raise RuntimeError("no usable messages in run")
    os.makedirs(out_dir, exist_ok=True)
    files, missing = {}, {}
    ny, nx = win["ny"], win["nx"]
    for f, spec in FIELDS.items():
        sent = MISS16 if spec["dtype"] == "int16" else MISSU16
        blk = np.full((len(hours), ny, nx), sent, dtype="<i2" if spec["dtype"] == "int16" else "<u2")
        for h, arr in enumerate(data[f]):
            if arr is not None: blk[h] = encode(f, arr)
        raw = blk.tobytes(); gz = gzip.compress(raw, 9, mtime=0)
        name = f"{f}.bin.gz"
        with open(os.path.join(out_dir, name), "wb") as fp: fp.write(gz)
        files[f] = {"file": name, "rawBytes": len(raw), "gzipBytes": len(gz), "sha256": hashlib.sha256(gz).hexdigest()}
        missing[f] = [hours[h] for h, a in enumerate(data[f]) if a is None and not (f.startswith("apcp") and hours[h] == 0)]
    grid_raw = np.stack([geo["lat"], geo["lon"]]).astype("<f4").tobytes(); grid_gz = gzip.compress(grid_raw, 9, mtime=0)
    with open(os.path.join(out_dir, "latlon.bin.gz"), "wb") as fp: fp.write(grid_gz)
    manifest = {
        "schema": 1, "version": VERSION, "model": "HRRR", "domain": "CONUS 3 km", "cycle": cycle.strftime("%Y-%m-%dT%H:00:00Z"),
        "hours": hours, "valid": [(cycle + dt.timedelta(hours=fh)).strftime("%Y-%m-%dT%H:00:00Z") for fh in hours],
        "box": BOX, "grid": {"projection": "lambert_conformal_conic (GRIB2 template 3.30)", **{k: proj[k] for k in GRID_KEYS}, **win,
                             "georefMaxErrorCells": geo["maxErrorCells"], "checkpoints": checks, "corners": geo["corners"], "latlon": {"file": "latlon.bin.gz", "gzipBytes": len(grid_gz), "layout": "float32[2][ny][nx] (lat, lon)"}},
        "fields": {f: {**FIELDS[f], **files[f], "missingHours": missing[f], "layout": f"{FIELDS[f]['dtype']}[hours][ny][nx] little-endian; value = stored * scale; missing = {MISS16 if FIELDS[f]['dtype'] == 'int16' else MISSU16}",
                       "intervals": [m if m else None for m in meta[f]]} for f in FIELDS},
        "precipNotes": "apcp_hour holds the GRIB (N-1)-N h accumulation and apcp_run the GRIB 0-N h accumulation, each read from the message's own startStep/endStep. "
                       "Longer windows are sums of consecutive apcp_hour fields from this run only and are unavailable unless every hour in the window is present. f00 has no accumulation.",
        "sources": sources, "gribBytesDownloaded": grib_bytes, "browserPayloadBytes": sum(x["gzipBytes"] for x in files.values()) + len(grid_gz),
        "processedAt": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "attribution": "NOAA NCEP HRRR via NOAA Open Data Dissemination (AWS). U.S. Government work; not an official forecast.",
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as fp: json.dump(manifest, fp, separators=(",", ":"))
    log(f"{cycle:%Y%m%d%H}: {len(hours)} hours, grid {nx}x{ny}, georef error {geo['maxErrorCells']} cells, GRIB {grib_bytes/1e6:.1f} MB -> payload {manifest['browserPayloadBytes']/1e6:.2f} MB, missing {missing}")
    return manifest


def carry(site_url, rel, dest, files):
    """Copy an already-published package from the live site, verifying every file's hash."""
    os.makedirs(dest, exist_ok=True)
    for name, sha in files.items():
        b = http(f"{site_url}/{rel}/{name}")
        if sha and hashlib.sha256(b).hexdigest() != sha: raise RuntimeError(f"hash mismatch for {rel}/{name}")
        with open(os.path.join(dest, name), "wb") as fp: fp.write(b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, help="output directory for the complete site")
    ap.add_argument("--published", default="https://wxman33.github.io/forecast-ops-maps", help="live site, for carrying forward retained runs")
    ap.add_argument("--now", default=None, help="UTC time override, ISO")
    ap.add_argument("--only", default=None, help="process just this cycle YYYYMMDDHH with --hours")
    ap.add_argument("--hours", type=int, default=None)
    ap.add_argument("--changed-output", default=None, help="file to append changed=true|false (GitHub Actions output)")
    a = ap.parse_args()
    now = dt.datetime.fromisoformat(a.now).replace(tzinfo=UTC) if a.now else dt.datetime.now(UTC)
    data_dir = os.path.join(a.site, "data", "hrrr"); os.makedirs(data_dir, exist_ok=True)
    if a.only:
        c = dt.datetime.strptime(a.only, "%Y%m%d%H").replace(tzinfo=UTC)
        process_run(c, a.hours or (48 if c.hour in EXTENDED else 18), os.path.join(data_dir, a.only)); return
    try: prev = json.loads(http(f"{a.published}/data/index.json"))
    except Exception: prev = {"runs": []}
    have = {r["id"]: r for r in prev.get("runs", [])}
    keep = plan(available(now), now)
    if not keep: raise SystemExit("no usable HRRR run found")
    runs, failures = [], []
    for c, role, n in keep:
        rid = c.strftime("%Y%m%d%H"); dest = os.path.join(data_dir, rid); rel = f"data/hrrr/{rid}"
        try:
            if rid in have and have[rid].get("hoursExpected") == n:
                m0 = json.loads(http(f"{a.published}/{rel}/manifest.json"))
                carry(a.published, rel, dest, {**{v["file"]: v["sha256"] for v in m0["fields"].values()}, "latlon.bin.gz": None})
                with open(os.path.join(dest, "manifest.json"), "w") as fp: json.dump(m0, fp, separators=(",", ":"))
                m = m0; how = "carried forward"
            else:
                m = process_run(c, n, dest); how = "processed"
            runs.append({"id": rid, "cycle": m["cycle"], "role": role, "hoursExpected": n, "hours": len(m["hours"]), "path": rel,
                         "missing": {f: v["missingHours"] for f, v in m["fields"].items() if v["missingHours"]},
                         "gribBytesDownloaded": m["gribBytesDownloaded"], "browserPayloadBytes": m["browserPayloadBytes"], "how": how})
        except Exception as e:
            failures.append({"id": rid, "error": str(e)[:300]})
            if rid in have:  # keep the previously published copy rather than lose the run
                try:
                    m0 = json.loads(http(f"{a.published}/{rel}/manifest.json"))
                    carry(a.published, rel, dest, {**{v["file"]: v["sha256"] for v in m0["fields"].values()}, "latlon.bin.gz": None})
                    with open(os.path.join(dest, "manifest.json"), "w") as fp: json.dump(m0, fp, separators=(",", ":"))
                    runs.append({**have[rid], "how": "kept previous copy after failure"})
                except Exception as e2: failures.append({"id": rid, "error": "previous copy unavailable: " + str(e2)[:200]})
    if not runs: raise SystemExit("nothing publishable: " + json.dumps(failures))
    if failures and not any(r["role"].startswith("latest") for r in runs):
        raise SystemExit("newest run failed and no latest run is publishable; keeping the deployed site: " + json.dumps(failures))
    index = {"schema": 1, "version": VERSION, "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "model": "HRRR", "box": BOX,
             "staleAfterHours": 3, "runs": sorted(runs, key=lambda r: r["cycle"], reverse=True), "failures": failures,
             "retention": "newest usable run plus the two newest 48-h runs (00/06/12/18Z)"}
    with open(os.path.join(a.site, "data", "index.json"), "w") as fp: json.dump(index, fp, indent=1)
    changed = sorted((r["id"], r["role"]) for r in runs) != sorted((r["id"], r["role"]) for r in prev.get("runs", [])) or any(r["how"] != "carried forward" for r in runs)
    if a.changed_output:
        with open(a.changed_output, "a") as fp: fp.write(f"changed={'true' if changed else 'false'}\n")
    print(json.dumps({k: index[k] for k in ("generatedAt", "failures")}), "changed" if changed else "unchanged", [(r["id"], r["role"], r["how"]) for r in index["runs"]])


if __name__ == "__main__":
    main()
