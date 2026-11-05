# HRRR product inventory (Tri-State model maps)

Generated from the NOAA HRRR CONUS index files (`wrfsfcfNN`, `wrfprsfNN`, 18Z 4 Nov 2026 run). Sizes are the measured GRIB2 message sizes for the full CONUS field at f06; the pipeline downloads only these byte ranges and crops to the Tri-State. Status: **live** = in the viewer now; direct = a GRIB field; derived = a documented calculation from direct fields of the same run; needs additional data = requires fields or services not yet collected; unavailable = not in the HRRR public files. Unsupported products stay out of the active menu.

Index sizes: wrfsfc 167 field/level pairs (~150 MB per hour for the whole file), wrfprs 705 pairs (~420 MB per hour). Forecast hours: f00–f18 every run; to f48 at 00/06/12/18Z; accumulations and hourly maxima start at f01.


## Radar & clouds

| Product | Status | GRIB field / level | Interval | Hours | MB per hour (CONUS) | Notes |
|---|---|---|---|---|---|---|
| Composite reflectivity | **live in v1** | REFC entire atmosphere | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.64 |  |
| 1 km AGL reflectivity (the WeatherBell reference field) | direct | REFD 1000 m above ground | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.45 |  |
| 4 km AGL reflectivity | direct | REFD 4000 m above ground | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.19 |  |
| Hourly max 1 km reflectivity | direct | MAXREF 1000 m above ground | max over (N−1)–N h | f01+ (no value at f00) | 0.46 |  |
| Echo tops | direct | RETOP cloud top | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.29 |  |
| Vertically integrated liquid | direct | VIL entire atmosphere | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.45 |  |
| Precipitation type (rain/snow/ice pellets/freezing rain) | direct | CRAIN, CSNOW, CICEP, CFRZR surface | instantaneous categorical | f00–f18 every run; to f48 at 00/06/12/18Z | 0.10 | combine with reflectivity for typed radar |
| Total / low / mid / high cloud cover | **total live; others direct** | TCDC, LCDC, MCDC, HCDC | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 2.17 |  |
| Simulated IR / water-vapour satellite | direct | SBT113, SBT114, SBT123, SBT124 top of atmosphere | instantaneous brightness temperature | f00–f18 every run; to f48 at 00/06/12/18Z | 2.41 | channel mapping to GOES bands to be confirmed before labelling |
| Lightning threat | direct | LTNG entire atmosphere | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.04 |  |

## Surface

| Product | Status | GRIB field / level | Interval | Hours | MB per hour (CONUS) | Notes |
|---|---|---|---|---|---|---|
| 2-m temperature | **live in v1** | TMP 2 m above ground | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 1.15 |  |
| 2-m dew point | **live: surface-v2** | DPT 2 m above ground | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 1.16 |  |
| 2-m relative humidity | direct | RH 2 m above ground | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 1.57 |  |
| 10-m wind (speed, barbs/streamlines) | **speed live; vectors later** | UGRD, VGRD 10 m above ground | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 4.53 | grid-relative components: rotate to earth-relative before display |
| Surface wind gust | **live: surface-v2** | GUST surface | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 1.24 |  |
| Hourly max 10-m wind | direct | MAXUW, MAXVW 10 m | max over (N−1)–N h | f01+ (no value at f00) | 2.63 |  |
| Mean sea-level pressure (isobars) | direct | MSLMA mean sea level | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.61 | MAPS reduction; label as such |
| Visibility | direct | VIS surface | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 1.42 |  |
| Heat index / wind chill / feels-like | derived (documented calculation) | TMP+RH (+wind) 2 m/10 m | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | — | NWS Rothfusz and 2001 wind-chill formulas; label as derived |
| Temperature change (1 h / 24 h) | derived (documented calculation) | TMP 2 m from the same run | difference | f00–f18 every run; to f48 at 00/06/12/18Z | — | 24 h needs f24+ (extended runs) |

## Precipitation

| Product | Status | GRIB field / level | Interval | Hours | MB per hour (CONUS) | Notes |
|---|---|---|---|---|---|---|
| Total precipitation since run start | **live in v1** | APCP surface 0–N h | 0–N h accumulation | f01+ (no value at f00) | 0.55 |  |
| 1 / 3 / 6 / 12 / 24 h precipitation | **live in v1 (1, 6, 24 h)** | APCP (N−1)–N h summed | explicit window; full window required | f01+ (no value at f00) | 0.55 |  |
| Precipitation rate | direct | PRATE surface | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.10 |  |
| Snowfall (model, variable density) | direct | ASNOW surface 0–N h | 0–N h accumulation | f01+ (no value at f00) | 0.03 | HRRR's own snow-depth accumulation |
| Snowfall 10:1 | derived (documented calculation) | WEASD 0–N h ×10 | 0–N h accumulation | f01+ (no value at f00) | 0.02 | ratio stated on the map |
| Snowfall Kuchera | needs additional data | WEASD + max column temperature (prs TMP 500–1000 mb) | 0–N h accumulation | f01+ (no value at f00) | — | needs ~22 pressure-level temperature fields per hour from wrfprs (~0.4 MB each); calculation documented in-app |
| Freezing rain accumulation | direct | FRZR surface 0–N h | 0–N h accumulation | f01+ (no value at f00) | 0.02 |  |
| Frozen precipitation fraction | direct | CPOFP surface | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.18 |  |
| Snow depth | direct | SNOD surface | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.05 |  |
| Precipitable water | direct | PWAT entire atmosphere | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 1.00 |  |

## Severe weather

| Product | Status | GRIB field / level | Interval | Hours | MB per hour (CONUS) | Notes |
|---|---|---|---|---|---|---|
| Surface-based CAPE / CIN | direct | CAPE surface | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.45 |  |
| Most-unstable CAPE (255–0 mb) | direct | CAPE 255-0 mb above ground | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.47 |  |
| Mixed-layer CAPE (90–0 mb) | direct | CAPE 90-0 mb above ground | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.41 |  |
| 0–3 km CAPE | direct | CAPE 0-3000 m above ground | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 1.00 |  |
| 0–1 km / 0–3 km storm-relative helicity | direct | HLCY 1000-0 m, 3000-0 m | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 3.13 |  |
| 0–1 km / 0–6 km bulk shear | direct | VUCSH, VVCSH 0-1000 m, 0-6000 m | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 9.76 |  |
| Storm motion (Bunkers) | direct | USTM, VSTM 0-6000 m | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 2.07 |  |
| 2–5 km updraft helicity (hourly max/min) | direct | MXUPHL / MNUPHL 5000-2000 m | max over (N−1)–N h | f01+ (no value at f00) | 0.07 |  |
| 0–2 km / 0–3 km updraft helicity | direct | MXUPHL 2000-0 m, 3000-0 m | max over (N−1)–N h | f01+ (no value at f00) | 0.08 |  |
| Max hail size (hourly) | direct | HAIL surface / entire atmosphere | max over (N−1)–N h | f01+ (no value at f00) | 0.00 |  |
| Max updraft speed (hourly) | direct | MAXUVV 100-1000 mb | max over (N−1)–N h | f01+ (no value at f00) | 0.79 |  |
| LCL / LFC / EL heights | direct | HGT adiabatic condensation / free convection / equilibrium level | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 2.88 |  |
| Lifted index | direct | LFTX 500-1000 mb, 4LFTX | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.94 |  |
| Significant tornado (fixed-layer STP) / supercell composite | derived (documented calculation) | MLCAPE, LCL, 0–1 km SRH, 0–6 km shear (+MUCAPE, 0–3 km SRH for SCP) | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | — | fixed-layer forms only; the effective-layer versions need effective inflow layers that HRRR does not provide |
| Energy-helicity index | derived (documented calculation) | CAPE + HLCY | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | — |  |
| Significant hail parameter (SHIP) | needs additional data | MUCAPE, lapse rates, 500 mb T, shear | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | — | needs 700/500 mb temperatures from wrfprs |

## Upper air

| Product | Status | GRIB field / level | Interval | Hours | MB per hour (CONUS) | Notes |
|---|---|---|---|---|---|---|
| Heights / temperature / winds at 250–925 mb | direct | HGT, TMP, UGRD, VGRD (wrfprs; 40 levels 50–1000 mb) | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 15.10 | MB for the six standard levels; 4 fields each |
| Relative humidity / dew point by level | direct | RH, DPT (wrfprs) | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 4.80 |  |
| Vertical velocity (omega) | direct | VVEL (wrfprs) | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.64 |  |
| Absolute vorticity | direct | ABSV (wrfprs) | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.49 |  |
| Lapse rates (700–500 mb, 0–3 km) | derived (documented calculation) | TMP and HGT by level | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | — |  |
| Point soundings (skew-T) | needs additional data | all wrfprs levels at a point | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | — | ~420 MB per forecast hour for the full file; would need a point extraction service |

## Comparisons

| Product | Status | GRIB field / level | Interval | Hours | MB per hour (CONUS) | Notes |
|---|---|---|---|---|---|---|
| Run-to-run change (same valid time) | derived (documented calculation) | two archived packages of the same field | per field | f00–f18 every run; to f48 at 00/06/12/18Z | 0.00 | uses retained runs; no extra download |
| Model-to-model difference | needs additional data | a second model on a common grid | per field | depends on model | — | after RRFS/GFS are added; regridding stated |
| Ensemble spread / probabilities | unavailable in HRRR | HRRR has no ensemble in this feed | — | — | — | RRFS ensemble / REFS are the candidates |

## Specialized

| Product | Status | GRIB field / level | Interval | Hours | MB per hour (CONUS) | Notes |
|---|---|---|---|---|---|---|
| Near-surface smoke | direct | MASSDEN 8 m above ground | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.39 | kg m⁻³ → µg m⁻³ |
| Vertically integrated smoke | direct | COLMD entire atmosphere | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 0.46 |  |
| Ceiling and flight category | derived (documented calculation) | HGT cloud ceiling + VIS | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 2.99 | FAA VFR/MVFR/IFR/LIFR thresholds |
| Boundary-layer height / ventilation | derived (documented calculation) | HPBL + mean PBL wind | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | 3.02 |  |
| Hot-dry-windy index | needs additional data | T, moisture, wind in the lowest 500 m | instantaneous | f00–f18 every run; to f48 at 00/06/12/18Z | — | needs native/low pressure levels |
| Icing / turbulence | unavailable in HRRR | not in HRRR public files | — | — | — |  |

## Processing and storage per added field (Tri-State crop)

Per forecast hour: about 0.05–2.5 MB downloaded (the size column), about 2–3 s decode/crop; browser payload about 15–40 KB compressed. For an 18-h run that is roughly 1–45 MB downloaded and 0.3–0.8 MB published per field; for a 48-h run about 2.5 times that. The current three fields cost ≈56 MB (18 h) / ≈170 MB (48 h) per run. A batch of ~12 surface/radar fields would be ≈200 MB per 18-h run and ≈12 minutes of runner time, still within free public-repository Actions; the published site would grow to roughly 60–90 MB with the current retention.

