// Manual probe: the exact Single Runs request the archive builder makes (shared loader, timezone=auto).
global.window = undefined;
const C = require("../archive/vendor/ops-core.js"), D = require("../archive/vendor/ops-data.js");
(async () => {
  const cyc = Math.floor((Date.now() - 12 * 3600e3) / (12 * 3600e3)) * 12 * 3600e3, run = new Date(cyc).toISOString().slice(0, 16);
  for (const id of ["gfs_seamless", "ecmwf_ifs025", "ncep_nbm_conus"]) {
    const spec = { ...C.config.blend, base: "https://single-runs-api.open-meteo.com/v1/forecast", params: { ...C.config.blend.params, run } };
    const p = new URLSearchParams({ ...spec.params, forecast_days: 8, latitude: 39.04456, longitude: -84.67229, timezone: "auto", models: id, hourly: spec.hourly.join(",") });
    const r = await fetch(`${spec.base}?${p}`); const j = await r.json().catch(() => null);
    console.log(id, run, "HTTP", r.status, j && j.error ? "ERROR " + j.reason : `first=${j && j.hourly && j.hourly.time[0]} offset=${j && j.utc_offset_seconds} n=${j && j.hourly && j.hourly.time.length}`);
    const res = await D.loadModels(spec, { lat: 39.04456, lon: -84.67229, days: 8, ids: [id] }).catch((e) => ({ err: e.message }));
    console.log("  loader:", res.err || `models=${Object.keys(res.models || {})} ms0=${res.ms && new Date(res.ms[0]).toISOString()} cycle=${new Date(cyc).toISOString()} problems=${JSON.stringify(res.problems)}`);
  }
})();
