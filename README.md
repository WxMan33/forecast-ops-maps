# forecast-ops-maps

Builds and publishes the gridded model data behind the [Forecast Ops model maps](https://wxman33.github.io/forecast-ops/maps.html). Public model data only; no private verification data lives here.

## What it does (v1)

- **Model:** NOAA HRRR, CONUS 3 km, from the NOAA Open Data Dissemination bucket on AWS (`noaa-hrrr-bdp-pds`). Only the needed GRIB2 messages are fetched, by byte range from each file's `.idx`.
- **Fields:** 2-m temperature; simulated composite reflectivity (model, not radar); precipitation as the GRIB one-hour accumulation (N−1 to N h) and the GRIB run total (0 to N h). Every message is identified from its own GRIB metadata (shortName, level, stepType, startStep/endStep, units), not from the index text. Longer precipitation windows are computed in the viewer by adding consecutive one-hour fields from the same run, and are unavailable unless every hour exists. Missing data stays missing, never zero.
- **Region:** 37.6–40.7°N, 86.6–82.4°W, cropped as whole native grid cells (138×133). The Lambert conformal projection parameters from GRIB are stored with each run; the pipeline checks that projected cell centres reproduce GRIB's own latitudes/longitudes, and records eccodes' nearest-gridpoint answers at CVG and eight other places, which the viewer's tests reproduce.
- **Retention:** the newest usable run plus the two newest 48-hour runs (00/06/12/18Z), so a 48-hour view is always available between extended cycles. A run is published only once all of its forecast hours exist (or, if an hour never appears, a few hours later with that hour marked missing).
- **Publishing:** `.github/workflows/hrrr.yml` runs twice an hour, assembles the complete site (new runs processed; retained runs carried forward from the live site with SHA-256 checks), then deploys it as a GitHub Pages artifact. Paths are versioned by run (`data/hrrr/<YYYYMMDDHH>/`); runs are immutable once published. If anything fails, nothing is deployed and the previous site stays live. No data branch, no history growth.
- **Sizes:** about 2.5 MB of GRIB is downloaded per forecast hour (≈56 MB per 18-h run, ≈170 MB per 48-h run); the browser payload is about 1.4 MB (18-h) to 3.3 MB (48-h) compressed. Both are recorded in every manifest and shown in the viewer.

## Limits

GitHub Pages (about 1 GB per site, ~100 GB/month soft bandwidth) suits this personal pilot. It is not a hosting plan for OPE Weather Ops or any commercial service; that needs its own storage and serving plan. Pages deployments can take a few minutes, and scheduled workflows can start late.

## Local run

```bash
python -m pip install -r pipeline/requirements.txt
python pipeline/test_hrrr.py
python pipeline/hrrr.py --site _site
```
