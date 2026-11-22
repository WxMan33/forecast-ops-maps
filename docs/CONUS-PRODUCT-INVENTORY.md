# Full-CONUS model product inventory (Maps beta: HRRR and NBM)

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

## Commercial rights per field (Model Maps preview, 21 Nov 2026)

Decision rule: a field goes on the **commercial preview** only when its upstream dataset, terms and attribution are written down here and every asset drawn with it (basemap, labels, fonts, colours) is cleared below. Anything unresolved stays off.

Upstream dataset for every field below: **NOAA/NCEP HRRR CONUS 3 km**, `wrfsfcfNN.grib2`, bucket `noaa-hrrr-bdp-pds` (NOAA Open Data Dissemination on the AWS Registry of Open Data). Terms: <https://registry.opendata.aws/noaa-hrrr-pds/> (NOAA Open Data Dissemination; U.S. Government work, no U.S. copyright; no restriction on use or redistribution). Attribution text used on the map, PNG and manifests: **"Data: NOAA/NCEP HRRR · Forecast Ops output · Model guidance, not an official forecast · Not endorsed by NOAA"**.

| Field (id) | Exact GRIB source, checked per message | Derivation | Rendered map | PNG export | Downloadable data (our tiles) | Status |
|---|---|---|---|---|---|---|
| 2-m temperature (`t2m`) | TMP 2 m above ground, 0/0/0, instant, K | K → °F | yes | yes | yes | **ready** |
| Composite reflectivity, simulated (`refc`) | REFC entire atmosphere, 0/16/196, instant, dB | none; labelled "simulated, not radar" everywhere | yes | yes | yes | **ready** |
| Precipitation type (`ptype`) | CRAIN 0/1/33, CSNOW 0/1/36, CICEP 0/1/35, CFRZR 0/1/34; surface, instant, code table 4.222 | one category per cell; freezing rain > ice pellets > snow > rain (precedence in the legend note); F00 not applicable (HRRR's analysis sets no flags) | yes | yes | yes | **ready** |
| 1-h precipitation (`apcp1h`) | APCP surface 0/1/8, accum (stat. 1) over (N−1)–N h, kg m⁻² | mm → in; F00 not applicable | yes | yes | yes | **ready** |
| Precipitation since run start (`apcp_run`) | APCP surface 0/1/8, accum 0–N h, kg m⁻² | mm → in; F00 not applicable | yes | yes | yes | **ready** |
| Snowfall since run start, HRRR (`asnow_run`) | ASNOW surface 0/1/29 (NCEP local "Total Snowfall"; eccodes names it "unknown", identity checked by the number triple), accum 0–N h, m | m → in; HRRR's variable-density snow, not 10:1 | yes | yes | yes | **ready** |
| 1-h snowfall, HRRR (`asnow1h`) | ASNOW 0–N and 0–(N−1) of the same run (HRRR publishes no hourly ASNOW) | difference; drops ≤ 0.05 in (packing noise) set to 0, larger ones fail the hour; labelled as derived | yes | yes | yes | **ready** |
| 2-m dew point (`d2m`) | DPT 2 m above ground, 0/0/6, instant, K | K → °F | yes | yes | yes | **ready** (22 Nov) |
| Surface wind gust (`gust`) | GUST surface, 0/2/22, instant, m s⁻¹ | m/s → mph; labelled as HRRR's instantaneous gust at the valid time, not an hourly maximum | yes | yes | yes | **ready** (22 Nov) |
| Freezing rain since run start (`frzr_run`) | FRZR surface, 0/1/225 (NCEP local "Freezing Rain"), accum 0–N h, kg m⁻² | mm → in liquid equivalent; labelled "not ice thickness on surfaces"; F00 not applicable | yes | yes | yes | **ready** (22 Nov) |

**Values overlay (label points, 22 Nov).** Each field-hour also publishes `pNN.bin.gz`: the stored int16 values at 376 Natural Earth places (those inside the grid) plus every 16th native cell (HRRR: 112 × 66 = 7,392 cells, 48 km apart; NBM: 147 × 100, 40 km apart), described in the manifest's `points` block. The viewer writes these numbers on the map (cities at national zoom, towns plus a staggered sample of grid points zoomed in; dry zeros and missing cells left blank) and they go into the PNG when Values is on. They are the same stored values as the L0 tiles, and the CI spot check compares every one of them with the GRIB (7,719 per HRRR field-hour, 15,033 per NBM field-hour). Place names come from Natural Earth (public domain).

Verification of identity and units (live 16Z and 18Z 21 Nov 2026 runs): every reader rejects a message whose discipline/category/number, level type, statistical processing, step window or units differ (the first draft named the type flags 0/1/192–195 and was rejected by this check before anything was published). ASNOW's metre units were confirmed against the same run's new snow water equivalent: median snow-to-liquid ratio 11.9:1 (10th–90th percentile 6.9–15.4) over 4,528 cells with > 2 mm of new liquid. Accumulation windows: at F24 and F48 HRRR's .idx names the run total '0-1 day acc fcst' / '0-2 day acc fcst'; both wordings are looked up and the GRIB step keys decide (the first preview build missed those two hours and showed them as missing until this was fixed). 1-h and run-total APCP agree: run total(N) − run total(N−1) equals the 1-h field within 0.0004 in at F06, F25 and F48 of the 12Z 21 Nov run (108,000–174,000 wet cells). Every CI build compares the built tiles with the GRIB grid at 21 airports, each field's maximum and three random non-zero cells, on all three zoom levels (`pipeline/spotcheck_conus.py`), and republishes the previous data if any cell differs.

## NBM (National Blend of Models), 22 Nov 2026

Upstream dataset: **NOAA/NWS National Blend of Models v4.x, core CONUS 2.5 km**, `blend.tHHz.core.fFFF.co.grib2`, bucket `noaa-nbm-grib2-pds` (NOAA Open Data Dissemination; AWS Registry of Open Data <https://registry.opendata.aws/noaa-nbm/>). Terms: U.S. Government work, no U.S. copyright; NODD states no restriction on use or redistribution; attribute NOAA/NWS and do not imply endorsement. Attribution used on the map, PNG, comparison page and manifests: **"Data: NOAA/NWS National Blend of Models (NOAA Open Data Dissemination) · Forecast Ops output · Model guidance, not an official forecast · Not endorsed by NOAA"**.

Grid: Lambert conformal (LaD 25°N, LoV 265°E, tangent at 25°N), 2345 × 1597 cells at 2539.7 m, earth radius 6,371,200 m. NBM GRIB uses **alternative row scanning** (every second stored row runs east to west); the decoder reverses those rows (`hrrr.grid_values`). Without that, 17 of 46 test points read the wrong cell (CVG 34 °F instead of 66 °F); the pipeline's nearest-point checkpoint caught it before anything was published, and a synthetic regression test now covers it.

| Field (id) | Exact GRIB source, checked per message | Derivation | Hours | Status |
|---|---|---|---|---|
| 2-m temperature (`t2m`) | TMP 2 m above ground, `.idx` qualifier empty, 0/0/0, **template 4.0** (deterministic; the same name/level/step also carries an "ens std dev" message, template 4.2, which is rejected), instant, K | K → °F | F01–F24 hourly | **ready** |
| 1-h PoP (`pop1h`) | APCP surface, qualifier `prob >0.254:prob fcst 255/255`, 0/1/8, **template 4.9**, probabilityType 1, upper limit 254 × 10⁻³ = 0.254 kg m⁻² (0.01 in), accum (N−1)–N h | none: NBM's own percent (0–100 checked) | F01–F24 hourly | **ready** |
| 6-h PoP (`pop6h`) | same as 1-h PoP with accum (N−6)–N h | none; NBM posts 6-h windows ending 00/06/12/18Z valid time only (verified in the 12Z and 13Z 22 Nov runs), so other hours are published as not applicable with that reason | windows ending 00/06/12/18Z within F06–F24 | **ready** |

Retention: the two newest NBM runs with F01–F24 posted (hourly cycles), so the cycle comparison works. Size: temperature ≈ 2.1 MB, 1-h PoP ≈ 0.6 MB, 6-h PoP ≈ 0.8 MB per forecast hour; two runs ≈ 152 MB. NBM posts hourly to F36 and 3-hourly beyond; F25+ waits for R2 hosting (GitHub Pages' 1 GB guideline). Verified 22 Nov 2026: every cell checked (21 airports, each field's maximum and random non-zero cells, three levels) and every label point equals the GRIB value (`spotcheck_conus.py --model nbm`, also run in CI before publishing).

### Everything else drawn with the fields

| Asset | Source | Licence / terms | Required credit | Commercial preview |
|---|---|---|---|---|
| State lines | U.S. Census Bureau cartographic boundaries via us-atlas 3.0.1 | Census data: U.S. Government work; us-atlas code/packaging: ISC (`assets/maps/us-atlas-LICENSE`) | "U.S. Census Bureau boundaries via us-atlas" (on map and PNG) | cleared |
| Coastline, borders, lakes, city points | Natural Earth (via world-atlas 2.0.2 and NE 10m/50m v5.1.2) | public domain (<https://www.naturalearthdata.com/about/terms-of-use/>) | none required; credited anyway | cleared |
| Map label glyphs | Open Sans Semibold SDF glyphs (openmaptiles/fonts via MapLibre demotiles), self-hosted | Apache 2.0 (`assets/maps/fonts/open-sans-v1/LICENSE.txt`) | licence file shipped with the glyphs | cleared |
| Page and PNG fonts | Archivo Black (Google Fonts) for page headings; PNG text uses Archivo when the browser has it, else the system sans-serif | SIL Open Font License 1.1 (Google Fonts); system fonts are not embedded or redistributed | none in the image | cleared |
| Map library | MapLibre GL JS 4.7.1, self-hosted | BSD 3-Clause (`assets/vendor/maplibre-gl-LICENSE.txt`) | licence file shipped | cleared |
| Colour scales | Forecast Ops' own stops for temperature, reflectivity, precipitation, snow and type (CONUS viewer) | ours | "Colors and rendering: Forecast Ops" | cleared |
| Tri-State viewer precipitation colours (not used on the CONUS preview) | ColorBrewer Greens / YlGnBu / RdPu values | Apache 2.0; needs "Colors from www.ColorBrewer.org by Cynthia A. Brewer" | add the credit or replace before that viewer is sold | **review** (outside this preview) |

### Not on the commercial preview yet

| Item | Why |
|---|---|
| Snowfall 10:1 (WEASD × 10), Kuchera snowfall | fields not built yet; WEASD is verified (0/1/13, accum, kg m⁻²) |
| Hosting on Cloudflare R2 | pilot blocked: repository settings `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ACCOUNT_ID`, `R2_BUCKET` (and `R2_PUBLIC_URL`) are not set; R2 storage terms to review with the account |
| ECMWF IFS/AIFS open data | CC BY 4.0, commercial use allowed with attribution (see the next-batch proposal); not built |
| RRFS, GFS | NOAA open data like HRRR; not built (see the next-batch proposal) |

## Hosting: measured use and estimated R2 cost

Measured (21 Nov 2026, seven HRRR fields): **2.5 MB per forecast hour** published for all seven fields (temperature 1.34, reflectivity 0.53, run-total precipitation 0.29, 1-h precipitation 0.19, type 0.07, run-total snow 0.06, 1-h snow 0.04; wet days larger). Three retained runs ≈ 117 forecast hours ≈ **0.3 GB** (0.5 GB on stormy days). A full rebuild processed 540 field-hours in 193 s on a GitHub runner.

Requests (phone, measured on the preview with the one-file-per-tile layout): cold load 8, zoom to a metro 8–12, every hour of a run ready to loop 94–104 per field. The packed layout (one file per field and hour, read with HTTP range requests) groups a view's tiles into one request per tile row per hour; GitHub Pages already answers those with `206 Partial Content` (checked), and R2 must do the same.

R2 estimate, using Cloudflare's published R2 rates as last known (storage $0.015 per GB-month, writes/lists $4.50 per million, reads $0.36 per million, no egress fees; monthly free allowance 10 GB, 1 M writes, 10 M reads). **Confirm current rates on the Cloudflare account before relying on these.**

| Scale | Storage | Writes / month | Reads / month | Est. monthly cost |
|---|---|---|---|---|
| Pilot: HRRR, 7 fields, 3 runs, ~20 staff | 0.3–0.5 GB | ≈ 130 k (≈ 4,000 hour files a day + manifests) | ≈ 1–2 M (20 people × 10 sessions/day × ~200) | **$0** (inside the free allowance) |
| Expanded: HRRR ~30 fields + NBM, RRFS, GFS, ECMWF; ~200 users | 30–50 GB | ≈ 3 M | ≈ 50 M | **≈ $25** ($0.3–0.6 storage + $9 writes + $14 reads) |

Reads served from Cloudflare's cache on a custom domain are not R2 reads, so a cached public endpoint lowers the read line further. GitHub Pages is fine for a preview, but its terms are not meant for a commercial service and it has a 1 GB site guideline, which the expanded scale would exceed.

## Next batch proposal (22 Nov 2026; nothing built)

Order by value per unit of work, given what is verified today:

| # | Model | Availability (checked 22 Nov 2026) | Terms for a commercial product | First fields | Work |
|---|---|---|---|---|---|
| 1 | **RRFS** (Rapid Refresh Forecast System, NCEP operational) | operational 3 km CONUS file `rrfs.tHHz.2dfld.3km.fFFF.conus.grib2` with `.idx` in `noaa-rrfs-ops-pds` (not the pre-operational `noaa-rrfs-pds`); its `.idx` names match HRRR's (REFC, GUST, TMP/DPT 2 m, APCP 1-h and run, ASNOW, CSNOW…, MXUPHL, CAPE) | NOAA open data, same terms as HRRR; attribute "NOAA/NCEP RRFS" | the ten HRRR fields, then updraft helicity and CAPE | small: the HRRR readers with a new URL and grid check; NWS service change notices gave several implementation dates in 2026, so re-check the status line before calling it operational in the UI |
| 2 | **GFS** 0.25° | `noaa-gfs-bdp-pds`, `gfs.tHHz.pgrb2.0p25.fFFF` (+ `.idx`, 743 messages at F24) | NOAA open data, same terms | 2-m temperature, 6-h precipitation, MSLP, 500 mb height, snow | medium: regular lat/lon grid, so the pipeline and the viewer need an equirectangular projector beside Lambert conformal, and a CONUS crop |
| 3 | **ECMWF IFS (and AIFS) open data** | ECMWF opened its full real-time catalogue on 1 Oct 2025; the free open subset is 0.25° (≈ 25 km), with a 9 km extension announced for later in 2026 | **CC BY 4.0**: commercial reuse allowed with attribution (e.g. "Source: ECMWF, CC BY 4.0; modified by Forecast Ops"); high-volume delivery may carry service charges; data obtained earlier under research licences stays non-commercial | 2-m temperature, 6-h precipitation, MSLP, 500 mb height | medium: same lat/lon work as GFS plus ECMWF's own GRIB conventions; confirm the exact attribution wording on ECMWF's licence page before release |

Hosting gate: HRRR (up to ≈ 0.55 GB with three runs) + NBM (≈ 0.15 GB) already uses most of GitHub Pages' 1 GB guideline, so RRFS, GFS, ECMWF and NBM beyond F24 need the R2 pilot (blocked on repository settings) first.

Sources: ECMWF open data announcement <https://www.ecmwf.int/en/about/media-centre/news/2025/ecmwf-makes-its-entire-real-time-catalogue-open-all>; AWS registry for RRFS <https://registry.opendata.aws/noaa-rrfs/>; NWS SCN 26-48 (RRFS/REFS implementation) <https://www.weather.gov/media/notification/pdf_2026/scn26-048_Updated_RRFS_and_REFS_Implementation_aad.pdf>.
