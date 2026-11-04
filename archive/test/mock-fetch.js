// Test-only (synthetic): replaces fetch with Open-Meteo/NWS look-alike responses. MOCK_MODE=shifted makes
// single-run responses start one hour late (must be rejected); MOCK_PUBLISHED=dir serves a "live site".
const fs = require("fs"), path = require("path");
const H = 3600e3, NOW = Date.parse(process.env.MOCK_NOW || "2026-11-04T20:00:00Z");
const json = (o, s = 200) => ({ ok: s < 400, status: s, json: async () => o, text: async () => JSON.stringify(o), arrayBuffer: async () => Buffer.from(JSON.stringify(o)) });
global.fetch = async (url) => {
  const u = new URL(url), q = u.searchParams;
  if (process.env.MOCK_PUBLISHED && u.host === "published.test") { const f = path.join(process.env.MOCK_PUBLISHED, u.pathname); if (!fs.existsSync(f)) return json({ error: true }, 404); const b = fs.readFileSync(f); return { ok: true, status: 200, json: async () => JSON.parse(b), arrayBuffer: async () => b }; }
  if (u.pathname.endsWith("/static/meta.json")) { const t = Math.floor((NOW - 6 * H) / (6 * H)) * 6 * H; return json({ last_run_initialisation_time: t / 1000, last_run_availability_time: (t + 5 * H) / 1000, update_interval_seconds: 21600 }); }
  if (u.host === "single-runs-api.open-meteo.com") {
    const run = Date.parse(q.get("run") + ":00Z") + (process.env.MOCK_MODE === "shifted" ? H : 0), n = +q.get("forecast_days") * 24, off = -18000, model = q.get("models");
    const time = Array.from({ length: n }, (_, i) => new Date(run + i * H + off * 1000).toISOString().slice(0, 16));
    const bump = (Date.parse(q.get("run") + ":00Z") - Date.parse("2026-11-02T00:00Z")) / (12 * H); // later runs: warmer, wetter, earlier
    const hourly = { time };
    (q.get("hourly") || "").split(",").forEach((v) => { hourly[v] = time.map((_, i) => { const t = run + i * H, hr = new Date(t + off * 1000).getUTCHours(), d = Math.floor((t + off * 1000) / 86400e3);
      if (v === "temperature_2m") return 45 + 10 * Math.sin(((hr - 9) / 24) * 2 * Math.PI) + bump * 0.8 + (model.length % 3);
      if (v === "precipitation") return d % 3 === 0 && hr >= 14 - Math.round(bump) && hr <= 18 - Math.round(bump) ? 0.05 + bump * 0.01 : 0;
      if (v === "snowfall") return 0; return v.includes("probability") ? 30 : 10; }); });
    return json({ latitude: 39.04, longitude: -84.67, timezone: "America/New_York", utc_offset_seconds: off, hourly_units: {}, hourly });
  }
  if (u.host === "ensemble-api.open-meteo.com") {
    const n = +q.get("forecast_days") * 24, off = -18000, start = Math.floor(NOW / 86400e3) * 86400e3 - off * 1000, time = Array.from({ length: n }, (_, i) => new Date(start + i * H + off * 1000).toISOString().slice(0, 16));
    const hourly = { time }; (q.get("hourly") || "").split(",").forEach((v) => { for (let m = 0; m < 5; m++) hourly[m ? `${v}_member${String(m).padStart(2, "0")}` : v] = time.map((_, i) => (v === "temperature_2m" ? 50 + m : 0)); });
    return json({ timezone: "America/New_York", utc_offset_seconds: off, hourly });
  }
  if (u.host === "api.weather.gov") return json({ error: true }, 503);
  return json({ error: true, reason: "mock: unknown " + u.host }, 404);
};
