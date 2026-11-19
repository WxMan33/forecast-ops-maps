# forecast-ops-maps

Builds and publishes the gridded model data behind the [Forecast Ops model maps](https://wxman33.github.io/forecast-ops/maps.html). Public model data only; no private verification data lives here.

## What it does (surface-v2)

- **Model:** NOAA HRRR, CONUS 3 km, from the NOAA Open Data Dissemination bucket on AWS (`noaa-hrrr-bdp-pds`). Only the needed GRIB2 messages are fetched, by byte range from each file's `.idx`.
- **Fields:** 2-m temperature and dew point (°F); 10-m wind speed and surface gusts (mph); entire-atmosphere total cloud cover (%); simulated composite and 1-km AGL reflectivity (model, not radar); precipitation as the GRIB one-hour accumulation (N−1 to N h) and the GRIB run total (0 to N h). Every message is identified from its own GRIB metadata (shortName, level, stepType, startStep/endStep, units), not from the index text. Longer precipitation windows are computed in the viewer by adding consecutive one-hour fields from the same run, and are unavailable unless every hour exists. Missing data stays missing, never zero.
- **Region:** 37.6–40.7°N, 86.6–82.4°W, cropped as whole native grid cells (138×133). The Lambert conformal projection parameters from GRIB are stored with each run; the pipeline checks that projected cell centres reproduce GRIB's own latitudes/longitudes, and records eccodes' nearest-gridpoint answers at CVG and eight other places, which the viewer's tests reproduce.
- **Retention:** the newest usable run plus the two newest 48-hour runs (00/06/12/18Z), so a 48-hour view is always available between extended cycles. A run is published only once all of its forecast hours exist (or, if an hour never appears, a few hours later with that hour marked missing).
- **Publishing:** `.github/workflows/hrrr.yml` runs twice an hour, assembles the complete site (new runs processed; retained runs carried forward from the live site with SHA-256 checks), then deploys it as a GitHub Pages artifact. Expanded packages use `data/hrrr/<YYYYMMDDHH>/surface-v2/`. Previously published package URLs are carried alongside the new package while that run remains retained. A failed upgrade falls back to the old complete package; partial output is removed before artifact upload. If no usable newest run can be retained, deployment stops and the previous site stays live. No data branch, no history growth.
- **Sizes:** every manifest records actual GRIB bytes downloaded and compressed package bytes. The measured Nov 5 00Z f00–f03 test downloaded 39.2 MB and produced 0.55 MB across all fields and coordinates. The viewer loads only the selected field, not the entire package. Full 18-/48-hour sizes vary with the weather and are logged by each build.
- **Definitions:** 10-m speed is `hypot(UGRD,VGRD)` from matching instantaneous components, then converted from m/s to mph. The norm does not require rotating grid-relative components; wind direction/barbs are not supplied. Gusts are the instantaneous surface gust diagnostic, not hourly maximum 10-m wind. Cloud cover is total through the atmosphere, not low cloud or ceiling. Exact level, units, valid hour and component basis are checked from GRIB metadata. New products have nine independently sampled GRIB checkpoints in `fieldCheckpoints`.
- **Memory:** cropped arrays are copied so they do not retain full CONUS arrays for each hour. Missing bitmap values become missing packed cells, and a missing wind component makes speed unavailable.

## Full-CONUS beta tiles (conus-v1)

`pipeline/conus.py` publishes the **whole** HRRR CONUS grid (1799 × 1059 native 3 km cells) for the Maps page's CONUS beta (`maps.html?domain=conus`). It reuses the Tri-State packager's byte-range fetching, GRIB metadata checks and projection code; nothing is resampled.

- **Levels:** L0 = every native cell at full precision (used when zoomed in and for every click-to-read value); L1 = every 2nd native cell, L2 = every 4th, rounded to the field's display step (0.5 °F for temperature). Fields whose extremes matter will use the block maximum instead (recorded per field as `coarse`).
- **Tiles:** 256 × 256 cells per tile, one file per tile per forecast hour (`data/conus/hrrr/<run>/conus-v1/<field>/fNN/L<level>_<x>_<y>.bin.gz`), so the browser downloads only the tiles in view at the level its screen needs. Encoding: gzip of zigzag-coded int16 row differences, split into low/high byte planes (exact; −32768 = missing).
- **Hours as they arrive:** the newest cycle is published hour by hour as NOAA posts it; files are immutable and carried forward from the live site with hash checks, so each build only processes new hours.
- **Retention:** newest cycle, newest complete run, newest complete 48-hour run (at most three runs).
- **Checks:** every native cell centre is projected and compared with GRIB's own latitude/longitude (largest difference must stay under 0.01 cell); eccodes' nearest-gridpoint values at 15 places from CVG to Seattle, Miami and Brownsville are recorded and reproduced by the viewer's tests.
- **Provenance:** every field records its source message, derivation, attribution and redistribution terms in each manifest.
- **Measured (2-m temperature, 19 Nov 2026):** about 1.04 MB per hour at L0, 0.19 MB at L1, 0.06 MB at L2; about 1.4 MB per field-hour in all. A from-scratch build of three runs (84 hours) takes about 70 s; an incremental build about 15 s. Three runs occupy about 120–170 MB of the Pages site.

Self-test: `python pipeline/test_conus.py`.

## Limits

GitHub Pages (about 1 GB per site, ~100 GB/month soft bandwidth) suits this personal pilot. It is not a hosting plan for OPE Weather Ops or any commercial service; that needs its own storage and serving plan. Pages deployments can take a few minutes, and scheduled workflows can start late.

## Local run

```bash
python -m pip install -r pipeline/requirements.txt
python pipeline/test_hrrr.py
python pipeline/hrrr.py --site _site
```

## CVG model-trend archive (`data/trends/CVG/`)

`archive/trends.js` keeps the five most recent 00Z/12Z cycles of point guidance at CVG, so the newest run can be compared with the previous run, 24 h earlier and 48 h earlier. It uses the forecast site's own loaders and snapshot code (vendored in `archive/vendor/`).

- **Deterministic models** (GFS, ECMWF IFS/AIFS, NBM, HRRR, RRFS 3 km, ICON, GEM, UKMO, Météo-France, JMA): retrieved from the Open-Meteo Single Runs API with `run=<cycle>`, accepted only when the response's time axis starts at that cycle and Open-Meteo's metadata shows the run complete (sources without metadata wait 10 h). These are **confirmed runs**; earlier cycles are backfilled.
- **Ensembles and NWS:** no run-specific retrieval exists (checked with `probe/run-identity.js`: Single Runs API 404, ensemble API rejects past `run=`). They are archived as **timestamped snapshots** with the provider-reported latest run or NWS update time, and are never labelled 00Z/12Z.
- Files are immutable and carried forward from the live site with SHA-256 checks. If an update fails, the previous archive is republished unchanged and the map deployment continues.
