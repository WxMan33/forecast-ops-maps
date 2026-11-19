# HRRR full-CONUS product inventory (Maps beta)

Every product on the CONUS maps records **source**, **derivation**, **attribution** and **redistribution terms** (also written into each run manifest by `pipeline/conus.py`). Status:

- **live**: published as conus-v1 tiles and shown in the viewer.
- **next batch**: a direct GRIB field or a documented same-run derivation; sized below; ready to add once hosting allows (see "Hosting").
- **derive**: needs a calculation that must be written down and checked before release.
- **license review**: the data or method comes from someone other than NOAA, or a name/brand is involved; terms must be checked before resale.
- **missing**: not in the public HRRR files; would need another source.

## Terms that apply to every HRRR product

| Item | Terms |
|---|---|
| Source | NOAA/NCEP High-Resolution Rapid Refresh (HRRR) CONUS 3 km, `wrfsfcfNN.grib2` / `wrfprsfNN.grib2`, from the NOAA Open Data Dissemination bucket `noaa-hrrr-bdp-pds` (AWS Registry of Open Data). Cycles hourly; f00–f18 every cycle, f00–f48 at 00/06/12/18 UTC. |
| Redistribution | U.S. Government work: no copyright restriction in the United States. NOAA Open Data Dissemination lists no restrictions on use or redistribution, including commercial. |
| Attribution | "NOAA/NCEP HRRR". Do not state or imply NOAA endorsement. Rendering, colours and legends are Forecast Ops' own. |
| Guarantee | None from NOAA. Model guidance, not an official forecast. |

Basemap (CONUS): US Census cartographic boundaries via us-atlas 3.0.1 (Census data public domain; us-atlas ISC licence); coast, borders, lakes and cities from Natural Earth (public domain). The Tri-State basemap's towns come from GeoNames (CC BY 4.0: attribution required).

## Measured size per forecast hour (full CONUS, 12Z 19 Nov 2026 run, f12)

Bytes as the pipeline would publish them (L0 full precision + L1/L2 display levels, gzip). Precipitation and radar fields grow on stormy days; temperature-like fields stay about constant.

| Product | GRIB bytes fetched | Published (L0 / L1 / L2) | Total MB per hour |
|---|---|---|---|
| 2-m temperature | 1.20 MB | 1.06 / 0.20 / 0.06 | 1.32 |
| 2-m dew point | 1.19 MB | 1.09 / 0.21 / 0.06 | 1.36 |
| 10-m wind speed | 4.76 MB | 1.26 / 0.18 / 0.05 | 1.49 |
| Surface gust | 1.25 MB | 1.23 / 0.19 / 0.05 | 1.47 |
| Composite reflectivity (simulated) | 0.63 MB | 0.35 / 0.07 / 0.03 | 0.45 |
| 1 km AGL reflectivity (simulated) | 0.42 MB | 0.26 / 0.06 / 0.02 | 0.34 |
| Precipitation rate | 0.10 MB | 0.08 / 0.03 / 0.01 | 0.12 |
| 1-h precipitation | 0.29 MB | 0.17 / 0.03 / 0.01 | 0.22 |
| Precipitation since run start | 0.63 MB | 0.44 / 0.08 / 0.03 | 0.55 |
| Precipitation type (4 categories) | 0.11 MB | 0.05 / 0.02 / 0.01 | 0.08 |
| Snowfall (HRRR ASNOW) | 0.21 MB | 0.11 / 0.02 / 0.01 | 0.14 |
| Snowfall 10:1 (from WEASD) | 0.22 MB | 0.10 / 0.02 / 0.01 | 0.13 |
| **All twelve** | **11.0 MB** | | **7.7** |

## Daily-use fields

| Product | Status | Source (GRIB) | Derivation | Notes |
|---|---|---|---|---|
| 2-m temperature | **live** | TMP 2 m above ground, instant | K → °F; L0 0.1 °F, L1/L2 0.5 °F | |
| 2-m dew point | next batch | DPT 2 m above ground | K → °F | |
| 10-m wind speed | next batch | UGRD, VGRD 10 m | hypot(u, v), m/s → mph; block max on L1/L2 | rotation-invariant; for barbs/arrows rotate grid-relative components to earth-relative (derive) |
| 10-m wind direction / barbs | derive | UGRD, VGRD 10 m | rotate by the Lambert convergence angle at each cell | must be verified against GRIB `uvRelativeToGrid` before release |
| Surface gust | next batch | GUST surface, instant | m/s → mph; block max | instantaneous gust diagnostic, not hourly maximum |
| Hourly max 10-m wind | next batch | MAXUW, MAXVW 10 m, (N−1)–N h max | hypot | f01+ only |
| Composite reflectivity | next batch | REFC entire atmosphere | dBZ; block max | simulated, not radar |
| 1 km AGL reflectivity | next batch | REFD 1000 m above ground | dBZ; block max | simulated, not radar |
| Precipitation rate | next batch | PRATE surface | kg m⁻² s⁻¹ → in/h; block max | |
| 1-h precipitation | next batch | APCP surface (N−1)–N h | mm → in | interval read from GRIB startStep/endStep |
| 3/6/12/24-h and run-total precipitation | next batch | APCP | sums of consecutive 1-h fields of the same run; run total from APCP 0–N | blank unless every hour in the window exists |
| Precipitation type | next batch | CRAIN, CSNOW, CICEP, CFRZR surface | one category per cell (freezing rain > ice pellets > snow > rain when several flags are set) | the precedence must be shown in the legend; combine with reflectivity for typed radar (derive) |
| Snowfall (HRRR) | next batch | ASNOW surface 0–N h | m → in | HRRR's variable-density snow |
| Snowfall 10:1 | next batch | WEASD surface 0–N h | water equivalent × 10 | ratio stated on the map |
| Snowfall Kuchera | derive (needs wrfprs) | WEASD + max temperature 500–1000 mb | Kuchera ratio from the column maximum temperature | about 22 pressure-level temperature fields per hour (~9 MB GRIB); published method, no licence needed |
| Freezing rain accumulation | next batch | FRZR surface 0–N h | kg m⁻² → in | |
| Total / low / mid / high cloud | later batch | TCDC, LCDC, MCDC, HCDC | % | |
| Visibility, ceiling, flight category | later batch / derive | VIS surface, HGT cloud ceiling | FAA VFR/MVFR/IFR/LIFR thresholds | |
| MSLP | later batch | MSLMA mean sea level | Pa → hPa; contours | MAPS reduction; labelled as such |
| Heat index / wind chill | derive | TMP, RH 2 m, wind 10 m | NWS Rothfusz regression; NWS 2001 wind chill | |

## Upper air and severe (batches after the daily-use fields)

| Product | Status | Source (GRIB) | Notes |
|---|---|---|---|
| Heights, temperature, winds at 250/500/700/850/925 mb | later batch | wrfprs HGT, TMP, UGRD, VGRD | about 15 MB GRIB per hour for six levels; contours + barbs need direction (derive) |
| Relative humidity / dew point by level | later batch | wrfprs RH, DPT | |
| Absolute vorticity, vertical velocity | later batch | wrfprs ABSV, VVEL | |
| SBCAPE / MLCAPE / MUCAPE / 0–3 km CAPE, CIN | later batch | wrfsfc CAPE, CIN at surface, 90-0 mb, 255-0 mb, 0-3000 m | |
| 0–1 / 0–6 km bulk shear | later batch | VUCSH, VVCSH 0-1000 m, 0-6000 m | magnitude is rotation-invariant |
| 0–1 / 0–3 km storm-relative helicity | later batch | HLCY 1000-0 m, 3000-0 m | |
| 2–5 km updraft helicity (hourly max) | later batch | MXUPHL 5000-2000 m | block max on coarse levels |
| 0–2 / 0–3 km updraft helicity, hourly max updraft, hail | later batch | MXUPHL, MAXUVV, HAIL | |
| Lifted index, LCL/LFC/EL heights | later batch | LFTX, HGT at adiabatic condensation / free convection / equilibrium level | |
| Fixed-layer STP, SCP, EHI | derive | MLCAPE, LCL, SRH, shear (+MUCAPE) | fixed-layer forms only; effective-layer versions need effective inflow layers HRRR does not provide |
| SHIP, lapse rates | derive (needs wrfprs) | 700/500 mb TMP, HGT, MUCAPE, shear | |
| Lightning threat | later batch | LTNG entire atmosphere | HRRR's own diagnostic, not observed lightning |
| Simulated satellite (IR/WV) | later batch, label check | SBT113/114/123/124 | channel-to-GOES-band mapping to be confirmed before labelling |
| Smoke (near-surface, column) | later batch | MASSDEN 8 m, COLMD | |
| Point soundings | missing (service needed) | all wrfprs levels at a point | ~420 MB GRIB per hour for the whole file; needs a point-extraction service |

## Missing from HRRR (other sources)

| Product | Candidate source | Status |
|---|---|---|
| Observed radar / MRMS products | NOAA MRMS (NODD) | missing; NOAA terms (public) |
| Observed lightning | NOAA GLM (GOES, NODD) for total lightning; commercial networks (Vaisala NLDN, Earth Networks) for cloud-to-ground | GLM missing (public); commercial networks need a licence: **license review** |
| Ensemble spread / probabilities | NOAA REFS / RRFS ensemble (NODD) | missing (public) |
| Other models (RRFS, NAM 3 km, GFS, NBM, ECMWF open data) | NODD for NOAA models; ECMWF open data is CC BY 4.0 | expand model by model; ECMWF: attribution required, **license review** for resale wording |
| Brand-name products or maps from subscription services | — | not reproduced; we build our own renderings from source data |

## Hosting (decision point)

GitHub Pages (about 1 GB per site) holds 2-m temperature for three runs (about 120–170 MB) alongside the Tri-State maps. The twelve daily-use fields at the current precision would need roughly 0.9 GB for the same three runs, and the busiest stormy days more. Options, measured costs and a recommendation are in the release notes for this beta.
