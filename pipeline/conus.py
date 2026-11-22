#!/usr/bin/env python3
"""HRRR full-CONUS map tiles for the Forecast Ops Maps beta (conus-v1).

Reuses the Tri-State packager's byte-range fetching, GRIB metadata checks and Lambert conformal code
(pipeline/hrrr.py) and publishes the WHOLE native HRRR grid (1799 x 1059 cells, 3 km), never resampled:

  L0  every native cell, stored at full precision (e.g. 0.1 degF). Used when zoomed in and for every
      click-to-read value, whatever the zoom.
  L1  every 2nd native cell in each direction (cells (2i, 2j)), rounded to the field's display step.
  L2  every 4th native cell (cells (4i, 4j)), rounded to the display step.
  Fields whose extremes matter (reflectivity, updraft helicity, ...) take the block maximum instead
  of the corner cell; the rule is written into each manifest ("coarse").

Each level is cut into 256 x 256-cell tiles, one file per tile per forecast hour, so the browser
downloads only the tiles in view at the level its screen needs. Hours are published as soon as NOAA
posts them: the newest cycle appears while it is still running and grows each build; files are
immutable once written and are carried forward from the live site with SHA-256 checks.

Levels 1 and 2 are stored in display steps (value = stored * scale * displayStep); level 0 in scale units.
Tile file: gzip of [low bytes][high bytes] of zigzag-coded row-wise differences of the int16 values
(difference d taken mod 2^16 as signed, then z = (d << 1) ^ (d >> 15)), row-major; decode = un-split,
un-zigzag, prefix-sum each row mod 2^16, read as int16. value = stored * scale;
stored == -32768 means missing (the difference coding is exact, so the sentinel survives).
"""
import argparse, concurrent.futures as cf, datetime as dt, gzip, hashlib, json, os, re, shutil, sys, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hrrr  # noqa: E402

PRODUCT = "conus-v1"
PACK = False   # --pack: one file per field and forecast hour (for object storage with HTTP range requests); see process_hour
TILE = 256
LEVELS = (0, 1, 2)
UTC = dt.timezone.utc
MISS = hrrr.MISS16
SITE_URL = "https://wxman33.github.io/forecast-ops-maps"
CHECKPOINTS = hrrr.CHECKPOINTS + [("Seattle (SEA)", 47.4502, -122.3088), ("Miami (MIA)", 25.7959, -80.2870), ("Boston (BOS)", 42.3656, -71.0096),
                                  ("San Diego (SAN)", 32.7338, -117.1933), ("Denver (DEN)", 39.8561, -104.6737), ("Brownsville (BRO)", 25.9068, -97.4259)]

import fields as FX  # noqa: E402  every field: its GRIB identity checks, units and derivation
FIELDS = FX.FIELDS


def available(now):
    """{cycle: {fh: lastModified}} for wrfsfc files posted today and yesterday (UTC)."""
    out = {}
    for d in (now.date(), now.date() - dt.timedelta(days=1)):
        token = None
        while True:
            q = f"{hrrr.BUCKET}/?list-type=2&prefix=hrrr.{d:%Y%m%d}/conus/hrrr.t" + (f"&continuation-token={hrrr.urllib.parse.quote(token)}" if token else "")
            x = hrrr.http(q).decode()
            for m in re.finditer(r"<Key>hrrr\.(\d{8})/conus/hrrr\.t(\d{2})z\.wrfsfcf(\d{2})\.grib2\.idx</Key><LastModified>([^<]+)</LastModified>", x):
                c = dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H").replace(tzinfo=UTC)
                out.setdefault(c, {})[int(m.group(3))] = m.group(4)
            t = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", x)
            if not t: break
            token = t.group(1)
    return out


def plan(avail, now, give_up_h=4.0):
    """Short retention: the newest cycle with any hour posted (may still be running), the newest
    complete run, and the newest complete 48-h run (00/06/12/18Z). At most three runs."""
    def n_of(c): return 48 if c.hour in hrrr.EXTENDED else 18
    def complete(c, hours):
        full = set(range(n_of(c) + 1))
        if full <= set(hours): return True
        return (now - c).total_seconds() / 3600 > (2.0 if n_of(c) == 18 else 2.5) + give_up_h and bool(full & set(hours))
    cyc = sorted(avail, reverse=True)
    keep = {}
    if cyc and 0 in avail[cyc[0]]: keep[cyc[0]] = "latest"
    done = [c for c in cyc if complete(c, avail[c])]
    if done: keep.setdefault(done[0], "complete")
    ext = [c for c in done if c.hour in hrrr.EXTENDED]
    if ext: keep[ext[0]] = keep[ext[0]] + "+extended" if ext[0] in keep else "extended"
    return [(c, keep[c], n_of(c), complete(c, avail[c])) for c in sorted(keep, reverse=True)]


def encode_tile(q):
    """int16 tile -> gzip bytes (row differences mod 2^16, byte planes split)."""
    u = q.astype("<i2").view("<u2").astype(np.int64)
    d = (u - np.concatenate([np.zeros((u.shape[0], 1), np.int64), u[:, :-1]], axis=1)) & 0xFFFF
    d = np.where(d >= 0x8000, d - 0x10000, d)                      # difference as a signed 16-bit number
    z = ((d << 1) ^ (d >> 15)) & 0xFFFF                              # zigzag: 0,-1,1,-2,... -> 0,1,2,3,...
    b = z.astype("<u2").view(np.uint8).reshape(z.shape[0], z.shape[1], 2)
    return gzip.compress(np.ascontiguousarray(b[..., 0]).tobytes() + np.ascontiguousarray(b[..., 1]).tobytes(), 6, mtime=0)


def decode_tile(gz, ny, nx):
    """Inverse of encode_tile (the viewer does the same in JavaScript)."""
    raw = np.frombuffer(gzip.decompress(gz), np.uint8)
    lo, hi = raw[: ny * nx].reshape(ny, nx).astype(np.int64), raw[ny * nx:].reshape(ny, nx).astype(np.int64)
    z = lo | (hi << 8); d = (z >> 1) ^ -(z & 1)
    return (np.cumsum(d, axis=1) & 0xFFFF).astype("<u2").view("<i2")


def level_grid(nx, ny, level):
    s = 2 ** level
    return (nx + s - 1) // s, (ny + s - 1) // s


def coarsen(q, level, rule, step):
    """Display level from the full-precision native array; missing stays missing."""
    if level == 0: return q
    s = 2 ** level
    if rule == "block max":
        ny, nx = q.shape; NY, NX = (ny + s - 1) // s, (nx + s - 1) // s
        p = np.full((NY * s, NX * s), MISS, np.int16); p[:ny, :nx] = q
        out = p.reshape(NY, s, NX, s).max(axis=(1, 3))
    else:
        out = q[::s, ::s].copy()
    if step > 1:   # coarse levels are stored in display steps: value = stored * scale * displayStep
        ok = out != MISS
        out = np.where(ok, np.round(out.astype(np.float64) / step).clip(-32767, 32767), MISS).astype(np.int16)
    return out


def tiles_for(q, level, rule, step):
    a = coarsen(q, level, rule, step); NY, NX = a.shape
    for ty in range(0, (NY + TILE - 1) // TILE):
        for tx in range(0, (NX + TILE - 1) // TILE):
            yield f"L{level}_{tx}_{ty}.bin.gz", a[ty * TILE:(ty + 1) * TILE, tx * TILE:(tx + 1) * TILE]


def fetch_field(url, fh, field, rows_of):
    """One field-hour from GRIB messages confirmed by their own metadata; returns (grib keys, display values, sources)."""
    return FIELDS[field]["read"](url, rows_of(fh)[1], fh, rows_of)


def grid_of(k):
    g = {x: k[x] for x in hrrr.GRID_KEYS}
    fwd = hrrr.lcc(g); x0, y0 = fwd(g["latitudeOfFirstGridPointInDegrees"], g["longitudeOfFirstGridPointInDegrees"])
    return {**g, "i0": 0, "j0": 0, "nx": g["Nx"], "ny": g["Ny"], "x0": float(x0), "y0": float(y0)}


def georef(buf, grid):
    """Every native cell centre from our projection must match GRIB's own lat/lon; plus nearest-point checkpoints."""
    saved = hrrr.CHECKPOINTS
    try:
        hrrr.CHECKPOINTS = CHECKPOINTS
        geo = hrrr.check_georef(buf, grid, grid)
        pts = hrrr.checkpoints(buf, grid)
    finally:
        hrrr.CHECKPOINTS = saved
    return {"maxErrorCells": geo["maxErrorCells"], "corners": geo["corners"], "checkpoints": pts}


def run_rows(cycle):
    """.idx rows per forecast hour of one run, fetched once and shared by every field (thread-safe enough: idempotent)."""
    cache = {}
    def rows_of(fh):
        if fh not in cache:
            url = f"{hrrr.BUCKET}/hrrr.{cycle:%Y%m%d}/conus/hrrr.t{cycle:%H}z.wrfsfcf{fh:02d}.grib2"
            cache[fh] = (url, hrrr.idx_rows(url))
        return cache[fh]
    return rows_of


def process_hour(cycle, fh, field, out_dir, grid=None, rows_of=None):
    """Fetch, verify and tile one field-hour. Returns (record, grid, georef or None)."""
    rows_of = rows_of or run_rows(cycle)
    url = rows_of(fh)[0]
    t0 = time.time()
    try:
        k, v, srcs = fetch_field(url, fh, field, rows_of)
    except FX.NotApplicable as e:
        return {"notApplicable": str(e)}, grid, None
    t1 = time.time()
    g = grid_of(k); geo = None
    if grid is None:
        r0 = srcs[0]["range"]; geo = georef(hrrr.http(url, tuple(r0)), g)
    elif g != grid: raise RuntimeError(f"grid changed within run at f{fh:02d}")
    run = dt.datetime.strptime(f"{k['dataDate']}{int(k['dataTime']):04d}", "%Y%m%d%H%M").replace(tzinfo=UTC)
    if run != cycle: raise RuntimeError(f"f{fh:02d} message is from run {run:%Y%m%d%H}")
    spec = FIELDS[field]; q = FX.encode(field, v)
    src = {"url": url, **srcs[0], **({"also": srcs[1:]} if len(srcs) > 1 else {})}
    files, pack = {}, None
    if PACK:
        # Packed: every tile is its own gzip member inside one file per field-hour, coarsest level first and each level
        # row by row, so a view's tiles in one row are one contiguous byte range. The manifest lists [offset, length].
        os.makedirs(os.path.join(out_dir, field), exist_ok=True)
        parts, names = [], []
        for L in sorted(LEVELS, reverse=True):
            for name, t in tiles_for(q, L, spec["coarse"], spec["displayStep"]):
                parts.append(encode_tile(t)); names.append(name)
        blob = b"".join(parts); rel = f"{field}/f{fh:02d}.pack"
        with open(os.path.join(out_dir, rel), "wb") as fp: fp.write(blob)
        # lengths in PACK_ORDER (levels coarsest first, each row by row); offsets are their running sum
        pack = {"file": rel, "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(), "lengths": [len(b) for b in parts]}
        sizes = dict(zip(names, pack["lengths"]))
    else:
        d = os.path.join(out_dir, field, f"f{fh:02d}"); os.makedirs(d, exist_ok=True)
        for L in LEVELS:
            for name, t in tiles_for(q, L, spec["coarse"], spec["displayStep"]):
                b = encode_tile(t)
                with open(os.path.join(d, name), "wb") as fp: fp.write(b)
                files[name] = [len(b), hashlib.sha256(b).hexdigest()[:16]]
        sizes = {n: v[0] for n, v in files.items()}
    pb = points_blob(q, grid or g); os.makedirs(os.path.join(out_dir, field), exist_ok=True)
    with open(os.path.join(out_dir, field, f"p{fh:02d}.bin.gz"), "wb") as fp: fp.write(pb)
    rec = {**({"pack": pack} if pack else {"files": files}), "points": [len(pb), hashlib.sha256(pb).hexdigest()[:16]], "bytes": {f"L{L}": sum(v for n, v in sizes.items() if n.startswith(f"L{L}_")) for L in LEVELS},
           "source": src, "seconds": {"download": round(t1 - t0, 2), "process": round(time.time() - t1, 2)},
           "missingCells": int((q == MISS).sum())}
    return rec, g, geo


PREV = None   # --prev DIR: the previous build's site (restored from the Actions cache); hours are copied from it when their hashes match


def fetch_carry(site_url, rel, name, size, check):
    """One already-published file: from the previous build's local copy when it matches, else from the live site."""
    if PREV:
        lp = os.path.join(PREV, rel, name)
        if os.path.exists(lp) and os.path.getsize(lp) == size:
            with open(lp, "rb") as fp: b = fp.read()
            if check(b): CARRY_STATS["local"] += 1; return b
    CARRY_STATS["http"] += 1
    return hrrr.http(f"{site_url}/{rel}/{name}")


CARRY_STATS = {"local": 0, "http": 0}


# ---- label points: exact native values for the viewer's Values overlay (cities + a regular grid), one small file per field-hour
LATTICE = 16   # every 16th native cell (48 km), offset by half a step
PLACES = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "label_places.json"), encoding="utf-8"))["places"]


def label_points(grid):
    """[(i, j)] for the places inside the grid, then the lattice row by row; plus the manifest description."""
    fwd = hrrr.lcc(grid); pl = []
    for name, lat, lon, pop in PLACES:
        x, y = fwd(lat, lon); i = int(round((float(x) - grid["x0"]) / grid["DxInMetres"])); j = int(round((float(y) - grid["y0"]) / grid["DyInMetres"]))
        if 0 <= i < grid["nx"] and 0 <= j < grid["ny"]: pl.append([name, lat, lon, pop, i, j])
    i0 = j0 = LATTICE // 2; nx = (grid["nx"] - 1 - i0) // LATTICE + 1; ny = (grid["ny"] - 1 - j0) // LATTICE + 1
    ij = [(p[4], p[5]) for p in pl] + [(i0 + LATTICE * a, j0 + LATTICE * b) for b in range(ny) for a in range(nx)]
    return ij, {"version": 1, "places": pl, "lattice": {"every": LATTICE, "i0": i0, "j0": j0, "nx": nx, "ny": ny},
                "encoding": "gzip of int16 little-endian values at L0 scale (value = stored * scale; -32768 = missing): places in order, then the lattice row by row (south to north, west to east)"}


def points_blob(q, grid):
    ij, _ = label_points(grid)
    I = np.array([a for a, _ in ij]); J = np.array([b for _, b in ij])
    return gzip.compress(q[J, I].astype("<i2").tobytes(), 6, mtime=0)


def points_from_tiles(out_dir, field, fh, rec, grid, levels):
    """Label points for an hour published before points existed: read from its own native (L0) tiles, which hold the
    stored values exactly (the same numbers points_blob would have written)."""
    ij, _ = label_points(grid); lv0 = next(l for l in levels if l["level"] == 0); vals = []
    cache = {}
    def tile(tx, ty):
        if (tx, ty) not in cache:
            ny = min(TILE, lv0["ny"] - ty * TILE); nx = min(TILE, lv0["nx"] - tx * TILE)
            if "pack" in rec:
                lens = rec["pack"]["lengths"]; names = [n for L in sorted(LEVELS, reverse=True) for n, _ in tiles_for(np.zeros((grid["ny"], grid["nx"]), np.int16), L, "corner cell", 1)]
                k = names.index(f"L0_{tx}_{ty}.bin.gz"); off = sum(lens[:k]); blob = open(os.path.join(out_dir, rec["pack"]["file"]), "rb").read()[off:off + lens[k]]
            else:
                blob = open(os.path.join(out_dir, field, f"f{fh:02d}", f"L0_{tx}_{ty}.bin.gz"), "rb").read()
            cache[(tx, ty)] = decode_tile(blob, ny, nx)
        return cache[(tx, ty)]
    for i, j in ij: vals.append(tile(i // TILE, j // TILE)[j % TILE, i % TILE])
    return gzip.compress(np.array(vals, "<i2").tobytes(), 6, mtime=0)


def carry_hour(site_url, rel, field, fh, rec, out_dir):
    if "points" in rec:
        n, sha = rec["points"]; name = f"{field}/p{fh:02d}.bin.gz"
        b = fetch_carry(site_url, rel, name, n, lambda x: hashlib.sha256(x).hexdigest()[:16] == sha)
        os.makedirs(os.path.join(out_dir, field), exist_ok=True)
        with open(os.path.join(out_dir, name), "wb") as fp: fp.write(b)
    if "pack" in rec:
        pk = rec["pack"]; b = fetch_carry(site_url, rel, pk["file"], pk["bytes"], lambda x: hashlib.sha256(x).hexdigest() == pk["sha256"])
        if len(b) != pk["bytes"] or hashlib.sha256(b).hexdigest() != pk["sha256"]: raise RuntimeError(f"hash mismatch for {rel}/{pk['file']}")
        os.makedirs(os.path.join(out_dir, field), exist_ok=True)
        with open(os.path.join(out_dir, pk["file"]), "wb") as fp: fp.write(b)
        return
    d = os.path.join(out_dir, field, f"f{fh:02d}"); os.makedirs(d, exist_ok=True)
    def one(item):
        name, (n, sha) = item
        b = fetch_carry(site_url, rel, f"{field}/f{fh:02d}/{name}", n, lambda x: hashlib.sha256(x).hexdigest()[:16] == sha)
        if len(b) != n or hashlib.sha256(b).hexdigest()[:16] != sha: raise RuntimeError(f"hash mismatch for {rel}/{field}/f{fh:02d}/{name}")
        with open(os.path.join(d, name), "wb") as fp: fp.write(b)
    with cf.ThreadPoolExecutor(8) as ex: list(ex.map(one, rec["files"].items()))


def build_run(cycle, role, n, complete, posted, out_dir, rel, old, site_url, workers=4, log=print):
    """Carry every hour already published for this run; process the newly posted ones."""
    hours = sorted(h for h in posted if h <= n)
    man = old or {}
    grid, geo = man.get("grid"), man.get("georef")
    if grid: grid = {k: v for k, v in grid.items() if k != "projection"}   # as grid_of() builds it, for the same-grid check
    fields = {f: {"hours": dict(man.get("fields", {}).get(f, {}).get("hours", {}))} for f in FIELDS}
    carried = processed = 0
    rows_of = run_rows(cycle)
    os.makedirs(out_dir, exist_ok=True)
    for f in FIELDS:
        todo = []
        for fh in hours:
            rec = fields[f]["hours"].get(str(fh))
            if rec:
                if "notApplicable" not in rec:
                    carry_hour(site_url, rel, f, fh, rec, out_dir)
                    if "points" not in rec and grid:   # published before the Values overlay: add its label points from its own tiles
                        lv = [{"level": L, **dict(zip(("nx", "ny"), level_grid(grid["nx"], grid["ny"], L)))} for L in LEVELS]
                        try:
                            pb = points_from_tiles(out_dir, f, fh, rec, grid, lv)
                        except (FileNotFoundError, ValueError) as e:   # no Values labels for this hour rather than a failed run
                            log(f"  {cycle:%Y%m%d%H} f{fh:02d} {f}: label points not derived ({str(e)[:80]})"); pb = None
                        if pb:
                            os.makedirs(os.path.join(out_dir, f), exist_ok=True)
                            with open(os.path.join(out_dir, f, f"p{fh:02d}.bin.gz"), "wb") as fp: fp.write(pb)
                            rec["points"] = [len(pb), hashlib.sha256(pb).hexdigest()[:16]]
                carried += 1
            else:
                todo.append(fh)
        while todo and grid is None:   # the first hour that has this field fixes and checks the grid for the run
            rec, grid, g0 = process_hour(cycle, todo[0], f, out_dir, None, rows_of); fields[f]["hours"][str(todo[0])] = rec; processed += 1; todo = todo[1:]
            geo = g0 or geo
        def work(fh):
            try: return fh, process_hour(cycle, fh, f, out_dir, grid, rows_of)[0], None
            except Exception as e: return fh, None, str(e)[:200]
        with cf.ThreadPoolExecutor(workers) as ex:
            for fh, rec, err in ex.map(work, todo):
                if rec: fields[f]["hours"][str(fh)] = rec; processed += 1
                else: log(f"  {cycle:%Y%m%d%H} f{fh:02d} {f}: {err}")
    have = sorted({int(h) for f in fields for h in fields[f]["hours"]})
    if not have: raise RuntimeError("no hours could be processed")
    levels = [{"level": L, "every": 2 ** L, **dict(zip(("nx", "ny"), level_grid(grid["nx"], grid["ny"], L))),
               "tilesX": (level_grid(grid["nx"], grid["ny"], L)[0] + TILE - 1) // TILE, "tilesY": (level_grid(grid["nx"], grid["ny"], L)[1] + TILE - 1) // TILE} for L in LEVELS]
    out = {
        "schema": 1, "product": PRODUCT, "model": "HRRR", "domain": "CONUS 3 km (full native grid)", "cycle": cycle.strftime("%Y-%m-%dT%H:00:00Z"),
        "role": role, "hoursExpected": n, "status": "complete" if complete and set(range(n + 1)) <= set(have) else ("final with gaps" if complete else "in progress"),
        "hours": have, "valid": {str(h): (cycle + dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:00:00Z") for h in have},
        "noaaPostedAt": {str(h): posted[h] for h in have if h in posted},
        "grid": {"projection": "lambert_conformal_conic (GRIB2 template 3.30)", **grid}, "georef": geo, "points": label_points(grid)[1],
        "tile": TILE, "levels": levels, "layout": "packed" if PACK else "tiles",
        **({"packOrder": "levels from coarsest to native; within a level, tile rows from south (ty=0) to north, tiles west to east; each tile a separate gzip member"} if PACK else {}),
        "encoding": "gzip of [low bytes][high bytes] of zigzag-coded int16 row differences (mod 2^16), row-major per tile; L0 value = stored * scale, L1/L2 value = stored * scale * displayStep; -32768 = missing",
        "fields": {f: {**{k: v for k, v in FIELDS[f].items() if k != "read"}, "missingSentinel": MISS, "hours": fields[f]["hours"]} for f in FIELDS},
        "updatedAt": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "attribution": "NOAA/NCEP HRRR via NOAA Open Data Dissemination (AWS). Rendered by Forecast Ops. Model guidance, not an official forecast.",
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as fp: json.dump(out, fp, separators=(",", ":"))
    log(f"{cycle:%Y%m%d%H} {role}: {len(have)}/{n + 1} hours ({out['status']}), {processed} processed, {carried} carried")
    return out, processed


def summary(man, rel):
    by = {L: 0 for L in ("L0", "L1", "L2")}
    for f in man["fields"].values():
        for rec in f["hours"].values():
            for L, b in rec.get("bytes", {}).items(): by[L] += b
    return {"id": man["cycle"][:13].replace("-", "").replace("T", ""), "cycle": man["cycle"], "role": man["role"], "status": man["status"], "hoursExpected": man["hoursExpected"],
            "hours": man["hours"], "path": rel, "fields": sorted(man["fields"]), "bytesByLevel": by, "updatedAt": man["updatedAt"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--published", default=SITE_URL)
    ap.add_argument("--now", default=None)
    ap.add_argument("--carry-only", action="store_true", help="republish the live CONUS data unchanged")
    ap.add_argument("--max-hours", type=int, default=None, help="limit hours per run (local tests)")
    ap.add_argument("--changed-output", default=None)
    ap.add_argument("--prev", default=None, help="previous build's site dir (Actions cache): carry hours from it when hashes match")
    ap.add_argument("--pack", action="store_true", help="one packed file per field-hour (object storage + HTTP range requests)")
    a = ap.parse_args()
    global PACK, PRODUCT, PREV
    PREV = a.prev if a.prev and os.path.isdir(a.prev) else None
    if a.pack: PACK, PRODUCT = True, "conus-v1-packed"
    now = dt.datetime.fromisoformat(a.now).replace(tzinfo=UTC) if a.now else dt.datetime.now(UTC)
    root = os.path.join(a.site, "data", "conus")
    try: prev = json.loads(hrrr.http(f"{a.published}/data/conus/index.json"))
    except Exception: prev = {"runs": []}
    have = {r["id"]: r for r in prev.get("runs", [])}
    t0 = time.time()
    if a.carry_only:
        runs = []
        for r in prev.get("runs", []):
            man = json.loads(hrrr.http(f"{a.published}/{r['path']}/manifest.json"))
            for f, spec in man["fields"].items():
                for fh, rec in spec["hours"].items(): carry_hour(a.published, r["path"], f, int(fh), rec, os.path.join(a.site, r["path"]))
            with open(os.path.join(a.site, r["path"], "manifest.json"), "w") as fp: json.dump(man, fp, separators=(",", ":"))
            runs.append(r)
        index = {**prev, "runs": runs}
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "index.json"), "w") as fp: json.dump(index, fp, indent=1)
        print("carried", [r["id"] for r in runs]); return
    avail = available(now)
    keep = plan(avail, now)
    if not keep: raise SystemExit("no HRRR cycle posted")
    runs, failures, processed = [], [], 0
    for c, role, n, complete in keep:
        rid = c.strftime("%Y%m%d%H"); rel = f"data/conus/hrrr/{rid}/{PRODUCT}"; dest = os.path.join(a.site, rel)
        posted = {h: t for h, t in avail[c].items() if a.max_hours is None or h <= a.max_hours}
        old = None
        if rid in have:
            try: old = json.loads(hrrr.http(f"{a.published}/{rel}/manifest.json"))
            except Exception: old = None
        try:
            man, p = build_run(c, role, n, complete, posted, dest, rel, old, a.published); processed += p
            runs.append(summary(man, rel))
        except Exception as e:
            failures.append({"id": rid, "error": str(e)[:300]}); shutil.rmtree(dest, ignore_errors=True)
    if not runs: raise SystemExit("nothing publishable: " + json.dumps(failures))
    index = {"schema": 1, "product": PRODUCT, "model": "HRRR", "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "runs": runs, "failures": failures,
             "retention": "newest cycle (published hour by hour as NOAA posts it), newest complete run, newest complete 48-h run (00/06/12/18Z)",
             "build": {"seconds": round(time.time() - t0, 1), "hoursProcessed": processed, "filesCarried": dict(CARRY_STATS)}}
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "index.json"), "w") as fp: json.dump(index, fp, indent=1)
    changed = processed > 0 or sorted((r["id"], r["role"]) for r in runs) != sorted((r["id"], r["role"]) for r in prev.get("runs", []))
    if a.changed_output:
        with open(a.changed_output, "a") as fp: fp.write(f"conus_changed={'true' if changed else 'false'}\n")
    print(json.dumps({"failures": failures, "build": index["build"]}), [(r["id"], r["role"], r["status"], len(r["hours"])) for r in runs])


if __name__ == "__main__":
    main()
