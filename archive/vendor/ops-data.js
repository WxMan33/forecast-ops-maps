/* Forecast Ops data loading: one way to request and align Open-Meteo data for every page,
   the overview, the live tests and any future server-side briefing. Browser (window.OpsData)
   or Node 18+ (require). Depends on ops-core.js. */
(function (root) {
  "use strict";
  const Core = root.OpsCore || (typeof require !== "undefined" ? require("./ops-core.js") : null);
  const S = Core.stats;

  /* GET JSON with a short session cache (browser only) and a 20 s timeout. */
  const IN_NODE = typeof window === "undefined";
  const RRFS = new Set(["ncep_rrfs_conus", "ncep_rrfs_conus_ensemble"]);
  // Raw repository endpoint updates when the scheduled data bot commits, without requiring
  // a second Pages deployment (GITHUB_TOKEN pushes do not trigger ordinary Pages builds).
  const RRFS_FEED = "https://raw.githubusercontent.com/WxMan33/forecast-ops/main/feeds/rrfs-cvg.json";

  /* NOAA native-grid fallback is deliberately limited to the CVG pilot. Never move a user's
     location, substitute a coarse grid or silently mix another model into a named RRFS source. */
  function directRrfsResponse(feed, model, params, loc, now = Date.now()) {
    if (!RRFS.has(model) || !feed || feed.schema !== 1 || feed.method !== "rrfs-cvg-native-v1") throw new Error("Unsupported RRFS point feed");
    const p = feed.point || {}, lat = +loc.lat, lon = +loc.lon;
    const km = Math.hypot((lat - p.lat) * 111.2, (lon - p.lon) * 86.4);
    if (!Number.isFinite(km) || km > 3) throw new Error("Native RRFS fallback is only available at CVG");
    const init = Date.parse(feed.cycle), made = Date.parse(feed.generatedAt);
    if (!Number.isFinite(init) || !Number.isFinite(made) || now - init > 16 * 3600e3 || now - made > 8 * 3600e3 || made > now + 5 * 60e3 || init > now) throw new Error("Native RRFS feed is stale or has invalid timestamps");
    const keys = model === "ncep_rrfs_conus" ? ["deterministic"] : ["member01", "member02", "member03", "member04", "member05"];
    const blocks = keys.map(k => feed.members && feed.members[k]);
    if (blocks.some(b => !b || !b.hourly || !Array.isArray(b.hourly.temperature_2m) || !b.hourly.temperature_2m.some(x => x != null && isFinite(x)))) throw new Error("Native RRFS member missing");
    const n = blocks[0].hourly.temperature_2m.length;
    if (n < 2 || blocks.some(b => b.hourly.temperature_2m.length !== n)) throw new Error("Native RRFS member axes differ");
    const tz = params.timezone === "GMT" || params.timezone === "UTC" ? "GMT" : "America/New_York";
    const offset = tz === "GMT" ? 0 : Core.time.offsetMin(now, tz) * 60;
    const times = Array.from({ length: n }, (_, i) => new Date(init + i * 3600e3 + offset * 1000).toISOString().slice(0, 16));
    const h = { time: times }, units = { time: "iso8601" };
    blocks.forEach((b, member) => {
      const g = b.grid || {};
      if (!(g.distance <= 3) || !Number.isFinite(g.lat) || !Number.isFinite(g.lon)) throw new Error("Native RRFS grid provenance missing");
      ["temperature_2m", "precipitation", "snowfall"].forEach(v => {
        const a = b.hourly[v]; if (!Array.isArray(a) || a.length !== n) throw new Error("Native RRFS field axis missing");
        const k = v + (member ? `_member${String(member).padStart(2, "0")}` : "");
        h[k] = a.map(x => x == null || !isFinite(x) ? null : v === "temperature_2m" ? params.temperature_unit === "fahrenheit" ? x * 9 / 5 + 32 : x : params.precipitation_unit === "inch" ? x / (v === "snowfall" ? 2.54 : 25.4) : x);
        units[k] = v === "temperature_2m" ? params.temperature_unit === "fahrenheit" ? "°F" : "°C" : params.precipitation_unit === "inch" ? "inch" : v === "snowfall" ? "cm" : "mm";
      });
    });
    const g = blocks[0].grid;
    return { latitude: g.lat, longitude: g.lon > 180 ? g.lon - 360 : g.lon, timezone: tz, utc_offset_seconds: offset, hourly: h, hourly_units: units,
      __dropped: [], __rrfs: { provider: "NOAA native GRIB2", method: feed.method, cycle: feed.cycle, generatedAt: feed.generatedAt, grid: g, elevationAdjusted: false, feedUrl: RRFS_FEED, snowMethod: feed.snowMethod } };
  }
  async function loadDirectRrfs(model, params, loc) {
    const feed = await getJSON(RRFS_FEED, 2);
    const out = directRrfsResponse(feed, model, params, loc);
    out.__retrievedAt = feed.__retrievedAt; out.__fromCache = feed.__fromCache;
    return out;
  }
  async function getJSON(url, cacheMin = 15, headers) {
    const key = "ops_c:" + url;
    let cache = null;
    try { cache = root.sessionStorage || null; } catch (e) { cache = null; }
    if (cacheMin && cache) {
      try { const c = JSON.parse(cache.getItem(key) || "null"); if (c && Date.now() - c.at < cacheMin * 60e3) { if (c.json && typeof c.json === "object") { c.json.__retrievedAt = new Date(c.at).toISOString(); c.json.__fromCache = true; } return c.json; } } catch (e) { /* no cache */ }
    }
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), 20000);
    let res;
    try { res = await fetch(url, headers ? { signal: ctl.signal, headers } : { signal: ctl.signal }); } finally { clearTimeout(to); }
    const json = await res.json().catch(() => ({ error: true, reason: `HTTP ${res.status}` }));
    if (!res.ok && !json.error) { json.error = true; json.reason = `HTTP ${res.status}`; }
    const at = Date.now();
    if (cacheMin && cache && !json.error) { try { cache.setItem(key, JSON.stringify({ at, json })); } catch (e) { /* full */ } }
    /* when this response was actually retrieved (the original time when served from the browser cache) */
    if (json && typeof json === "object") { json.__retrievedAt = new Date(at).toISOString(); json.__fromCache = false; }
    return json;
  }

  /* One model, one request. If Open-Meteo rejects a variable (or a whole pressure level) for
     this model, drop it and retry, so one unsupported field never sinks the whole model. */
  async function fetchModel(base, params, model, hourly, daily, loc) {
    // The native CVG feed is preferred when current; other locations keep their own API grid.
    if (RRFS.has(model) && Math.hypot((+loc.lat - 39.04456) * 111.2, (+loc.lon + 84.67229) * 86.4) <= 3) {
      try { return await loadDirectRrfs(model, params, loc); } catch (e) { /* try provider below */ }
    }
    let hv = hourly.slice(), dv = (daily || []).slice();
    const dropped = [];
    for (let attempt = 0; attempt < 8; attempt++) {
      const p = new URLSearchParams({ ...params, ...(RRFS.has(model) ? { cell_selection: "nearest", elevation: "nan" } : {}), latitude: loc.lat, longitude: loc.lon, timezone: "auto", models: model });
      if (hv.length) p.set("hourly", hv.join(","));
      if (dv.length) p.set("daily", dv.join(","));
      const json = await getJSON(`${base}?${p.toString().replace(/%2C/g, ",")}`);
      if (!json.error) {
        if (RRFS.has(model) && !S.hasData(json.hourly && json.hourly.temperature_2m)) {
          try { return await loadDirectRrfs(model, params, loc); } catch (e) { json.__rrfsUnavailable = e.message; }
        }
        json.__dropped = dropped; return json;
      }
      const why = String(json.reason || "");
      const levels = [...why.matchAll(/(\d{3,4})\s*hPa/gi)].map((m) => `_${m[1]}hPa`);
      const bad = [...hv, ...dv].filter((v) => why.includes(v) || levels.some((l) => v.endsWith(l)));
      if (!bad.length) throw new Error(why || "Request failed");
      dropped.push(...bad);
      hv = hv.filter((v) => !bad.includes(v)); dv = dv.filter((v) => !bad.includes(v));
    }
    throw new Error("Too many unsupported variables");
  }

  /* Pull named arrays out of a response, tolerating model-suffixed keys. */
  function pick(block, names) {
    const out = {};
    if (!block) return out;
    const keys = Object.keys(block);
    for (const n of names) {
      const k = keys.find((k) => k === n) || keys.find((k) => k.startsWith(n + "_") && !/_member\d+/.test(k));
      if (k) out[n] = block[k];
    }
    return out;
  }

  /* Heights always in meters. Open-Meteo returns geopotential height and freezing level in FEET
     whenever imperial units are requested (e.g. precipitation_unit=inch), so normalize here. */
  const LENGTH = /^(geopotential_height_|freezing_level_height)/;
  const unitOf = (j, v) => { const u = j.hourly_units || {}; const k = Object.keys(u).find((k) => k === v || k.startsWith(v + "_")); return k ? String(u[k]).toLowerCase() : ""; };
  function toMeters(v, arr, unit) {
    if (!arr || !LENGTH.test(v)) return arr;
    if (unit === "ft" || unit === "feet") return arr.map((x) => (x == null ? null : x * 0.3048));
    return arr;
  }

  /* Real instants for the provider's labels (labelling detected per response), then true local
     labels in the location's time zone for everything shown. labels = what the provider sent. */
  function withAxis(d) {
    const inst = Core.time.instants(d.times, d.tz, d.offset);
    d.labels = d.times;
    d.ms = inst.ms;
    d.mode = inst.mode;
    d.consistent = inst.consistent;
    d.times = inst.ms.map((m) => Core.time.toLocal(m, d.tz));
    return d;
  }

  /* ---------- NWS point forecasts ---------- */
  const NWS_HEADERS = IN_NODE ? { "User-Agent": "(forecast-ops, github.com/WxMan33/forecast-ops)", Accept: "application/geo+json" } : { Accept: "application/geo+json" };
  /* Point -> office/grid mapping, cached for a week in the browser and refreshed after that. */
  async function nwsPoint(lat, lon) {
    const key = `ops_nwspt:${(+lat).toFixed(4)},${(+lon).toFixed(4)}`;
    let store = null; try { store = root.localStorage || null; } catch (e) { store = null; }
    if (store) { try { const c = JSON.parse(store.getItem(key) || "null"); if (c && Date.now() - c.at < 7 * 86400e3) return c.json; } catch (e) { /* none */ } }
    const j = await getJSON(`https://api.weather.gov/points/${(+lat).toFixed(4)},${(+lon).toFixed(4)}`, 0, NWS_HEADERS);
    if (j && j.properties && j.properties.forecastGridData && store) { try { store.setItem(key, JSON.stringify({ at: Date.now(), json: j })); } catch (e) { /* full */ } }
    return j;
  }
  /* Everything NWS for a point. Never throws: failures come back as {ok:false, reason} so model tools keep working. */
  async function loadNWS({ lat, lon }) {
    try {
      const pt = await nwsPoint(lat, lon), pp = pt && pt.properties;
      if (!pp || !pp.forecastGridData) return { ok: false, reason: pt && pt.status === 404 ? "Outside NWS coverage" : `NWS points lookup failed${pt && pt.detail ? `: ${pt.detail}` : ""}` };
      const [fc, fh, gr] = await Promise.allSettled([getJSON(pp.forecast + "?units=us", 15, NWS_HEADERS), getJSON(pp.forecastHourly + "?units=us", 15, NWS_HEADERS), getJSON(pp.forecastGridData, 15, NWS_HEADERS)]);
      const val = (r) => (r.status === "fulfilled" && r.value && r.value.properties && !r.value.error ? r.value : null);
      const parts = { forecast: val(fc), hourly: val(fh), grid: val(gr) };
      const status = Core.nws.status(parts, Date.now());
      const errors = Object.keys(parts).filter((k) => !parts[k]).map((k) => `${k} request failed`)
        .concat(Object.keys(parts).filter((k) => parts[k] && status.stale[k]).map((k) => `${k} is stale (updated ${status.ageH[k].toFixed(0)} h ago)`));
      /* stale parts are dropped, never shown as current */
      Object.keys(parts).forEach((k) => { if (parts[k] && status.stale[k]) parts[k] = null; });
      const ok = Object.values(parts).some(Boolean);
      return { ok, reason: ok ? "" : errors.join("; ") || "NWS unavailable", office: pp.gridId, gridX: pp.gridX, gridY: pp.gridY, tz: pp.timeZone,
        place: pp.relativeLocation && pp.relativeLocation.properties ? `${pp.relativeLocation.properties.city}, ${pp.relativeLocation.properties.state}` : "",
        parts, status, errors, periods: Core.nws.periods(parts.forecast), hourly: Core.nws.hourly(parts.hourly) };
    } catch (e) { return { ok: false, reason: `NWS unavailable (${e.message || e})` }; }
  }

  /* Deterministic models for a config spec. ids: which models to request (default: all in the spec).
     Returns {times, dtimes, tz, models: {id: {h, d, elev}}, status: {id: "ok"|"failed"|"empty"}, problems[]} */
  async function loadModels(spec, { lat, lon, days, ids } = {}) {
    const list = spec.models.filter((m) => !ids || ids.includes(m.id));
    const params = { ...spec.params, forecast_days: days || spec.days };
    const res = await Promise.allSettled(list.map((m) => fetchModel(spec.base, params, m.id, [...spec.hourly, ...((spec.extraHourly || {})[m.id] || [])], spec.daily, { lat, lon })));
    const status = {}, problems = [], ok = [];
    res.forEach((r, i) => {
      const m = list[i];
      if (r.status !== "fulfilled") { status[m.id] = "failed"; problems.push(`${m.name}: request failed`); return; }
      const names = [...spec.hourly, ...((spec.extraHourly || {})[m.id] || [])];
      const h = pick(r.value.hourly, names), d = pick(r.value.daily, spec.daily || []);
      const absentMinimum = (m.minimumFields || []).filter(v => !S.hasData(h[v]));
      if (absentMinimum.length) { status[m.id] = "empty"; problems.push(`${m.name}: unavailable for ${spec.id}, missing ${absentMinimum.join(", ")}`); return; }
      if (!spec.require.some((v) => S.hasData(h[v]))) { status[m.id] = "empty"; problems.push(`${m.name}: no coverage at this point`); return; }
      const missing = spec.require.filter((v) => !S.hasData(h[v]));
      if (missing.length) problems.push(`${m.name}: no ${missing.join(", ")}`);
      status[m.id] = "ok";
      ok.push({ m, j: r.value, h, d, names });
    });
    if (!ok.length) return { times: [], dtimes: [], tz: null, models: {}, status, problems, empty: true };
    const master = ok.reduce((a, b) => (b.j.hourly.time.length > a.j.hourly.time.length ? b : a)).j;
    const times = master.hourly.time, dtimes = (master.daily && master.daily.time) || [];
    const models = {};
    ok.forEach(({ m, j, h, d, names }) => {
      const hh = {}, dd = {};
      names.forEach((v) => (hh[v] = S.align(times, j.hourly.time, toMeters(v, h[v], unitOf(j, v)))));
      (spec.daily || []).forEach((v) => (dd[v] = j.daily ? S.align(dtimes, j.daily.time, d[v]) : dtimes.map(() => null)));
      models[m.id] = { h: hh, d: dd, elev: j.elevation ?? null, provenance: j.__rrfs || null, retrievedAt: j.__retrievedAt || null, fromCache: !!j.__fromCache };
    });
    return withAxis({ times, dtimes, tz: master.timezone, offset: master.utc_offset_seconds, models, status, problems });
  }

  /* Ensembles for a config spec. Returns {times, tz, ens: {id: {var: [member arrays]}}, status, problems} */
  async function loadEnsemble(spec, { lat, lon, days, ids } = {}) {
    const list = spec.models.filter((m) => !ids || ids.includes(m.id));
    const params = { ...spec.params, forecast_days: days || spec.days };
    const res = await Promise.allSettled(list.map((m) => fetchModel(spec.base, params, m.id, spec.hourly, null, { lat, lon })));
    const status = {}, problems = [], ok = [];
    // Keep empty member arrays in their slots. Filtering each variable independently can shift
    // member identities and attach one member's snow to another member's temperature.
    const members = (block, v) => Object.keys(block || {}).filter((k) => (k === v || k.startsWith(v + "_")) && Array.isArray(block[k])).sort((a,b) => a.localeCompare(b)).map((k) => block[k]);
    res.forEach((r, i) => {
      const m = list[i];
      if (r.status !== "fulfilled") { status[m.id] = "failed"; problems.push(`${m.name}: unavailable`); return; }
      const byVar = {}; spec.hourly.forEach((v) => (byVar[v] = members(r.value.hourly, v)));
      if (!byVar[spec.require[0]].some(S.hasData)) { status[m.id] = "empty"; problems.push(`${m.name}: no members here`); return; }
      status[m.id] = "ok";
      ok.push({ m, j: r.value, byVar });
    });
    if (!ok.length) return { times: [], tz: null, ens: {}, status, problems, empty: true };
    const master = ok.reduce((a, b) => (b.j.hourly.time.length > a.j.hourly.time.length ? b : a)).j;
    const times = master.hourly.time, ens = {};
    ok.forEach(({ m, j, byVar }) => { ens[m.id] = {}; Object.keys(byVar).forEach((v) => (ens[m.id][v] = byVar[v].map((a) => S.align(times, j.hourly.time, a)))); });
    return withAxis({ times, tz: master.timezone, offset: master.utc_offset_seconds, ens, status, problems, sourceProvenance: Object.fromEntries(ok.filter(x => x.j.__rrfs).map(x => [x.m.id, x.j.__rrfs])),
      retrieved: Object.fromEntries(ok.map(x => [x.m.id, { at: x.j.__retrievedAt || null, fromCache: !!x.j.__fromCache }])) });
  }

  /* Open-Meteo's own run metadata for a model (static/meta.json). This is PROVIDER-REPORTED: the latest
     run Open-Meteo says it has, not a statement about which run produced a given forecast response. */
  async function loadModelMeta(metaIds) {
    const out = {};
    await Promise.all(Object.entries(metaIds).map(async ([id, metaId]) => {
      try {
        const j = await getJSON(`https://api.open-meteo.com/data/${metaId}/static/meta.json`, 15);
        if (j.error || !Number.isFinite(j.last_run_initialisation_time)) { out[id] = { error: j.reason || "no metadata" }; return; }
        out[id] = { metaId, lastRunInit: new Date(j.last_run_initialisation_time * 1000).toISOString(), lastRunAvailable: Number.isFinite(j.last_run_availability_time) ? new Date(j.last_run_availability_time * 1000).toISOString() : null,
          updateIntervalH: Number.isFinite(j.update_interval_seconds) ? j.update_interval_seconds / 3600 : null, retrievedAt: j.__retrievedAt || null };
      } catch (e) { out[id] = { error: String(e.message || e) }; }
    }));
    return out;
  }
  const OpsData = { getJSON, loadModelMeta, fetchModel, pick, loadModels, loadEnsemble, toMeters, loadNWS, withAxis, directRrfsResponse, loadDirectRrfs };
  if (typeof module !== "undefined" && module.exports) module.exports = OpsData;
  root.OpsData = OpsData;
})(typeof self !== "undefined" ? self : globalThis);

