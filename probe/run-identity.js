// One-off probe (manual workflow): which Open-Meteo endpoints return a SPECIFIC model run for CVG,
// and how far back. Prints status, the run the response says it is, and hour counts. No data stored.
const LAT = 39.04456, LON = -84.67229;
const DET = ["gfs_seamless", "ncep_gfs013", "ecmwf_ifs025", "ecmwf_aifs025_single", "ncep_nbm_conus", "gfs_hrrr", "ncep_hrrr_conus", "ncep_rrfs_conus", "icon_seamless", "gem_seamless", "ukmo_seamless"];
const ENS = ["ecmwf_ifs025_ensemble", "ncep_gefs025", "gfs025", "ncep_rrfs_conus_ensemble"];
const now = new Date(), d0 = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
const runs = [0, 1, 2, 3, 5].map((k) => new Date(d0 - k * 12 * 3600e3).toISOString().slice(0, 16)); // today 00Z and the 12-h steps back
async function get(url) { try { const r = await fetch(url, { signal: AbortSignal.timeout(30000) }); const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch (e) {} return { s: r.status, j, t: t.slice(0, 160) }; } catch (e) { return { s: "ERR " + e.message }; } }
const summ = (j) => { if (!j || !j.hourly) return ""; const k = Object.keys(j.hourly).find((x) => x.startsWith("temperature_2m")); const a = j.hourly[k] || []; return `hours=${a.length} valid=${a.filter((x) => x != null).length} first=${j.hourly.time && j.hourly.time[0]} keys=${Object.keys(j.hourly).length}${j.run ? " run=" + j.run : ""}${j.model_run ? " model_run=" + j.model_run : ""}`; };
(async () => {
  console.log("now", now.toISOString(), "runs probed", runs.join(", "));
  for (const m of DET) {
    for (const run of runs) {
      const u = `https://single-runs-api.open-meteo.com/v1/forecast?latitude=${LAT}&longitude=${LON}&models=${m}&run=${run}&hourly=temperature_2m,precipitation&temperature_unit=fahrenheit&forecast_days=3`;
      const r = await get(u); console.log(`single-runs det ${m} run=${run}: ${r.s} ${r.j && r.j.error ? "error " + r.j.reason : summ(r.j)}`);
    }
  }
  for (const m of ENS) {
    const run = runs[1];
    for (const host of ["single-runs-api.open-meteo.com/v1/ensemble", "ensemble-api.open-meteo.com/v1/ensemble"]) {
      const r = await get(`https://${host}?latitude=${LAT}&longitude=${LON}&models=${m}&run=${run}&hourly=temperature_2m&forecast_days=3`);
      console.log(`ens ${host.split("/")[0]} ${m} run=${run}: ${r.s} ${r.j && r.j.error ? "error " + r.j.reason : summ(r.j)}`);
    }
  }
  const pr = await get(`https://previous-runs-api.open-meteo.com/v1/forecast?latitude=${LAT}&longitude=${LON}&models=gfs_seamless&hourly=temperature_2m,temperature_2m_previous_day1,temperature_2m_previous_day2&forecast_days=2&past_days=2`);
  console.log(`previous-runs gfs_seamless: ${pr.s} ${pr.j && pr.j.error ? "error " + pr.j.reason : Object.keys((pr.j && pr.j.hourly) || {}).join(",")}`);
  for (const id of ["ncep_gfs013", "ecmwf_ifs025", "ncep_nbm_conus", "ncep_hrrr_conus", "ecmwf_ifs025_ensemble", "ncep_gefs025"]) {
    const r = await get(`https://api.open-meteo.com/data/${id}/static/meta.json`);
    const j = r.j || {}; console.log(`meta ${id}: ${r.s} last_run_init=${j.last_run_initialisation_time && new Date(j.last_run_initialisation_time * 1000).toISOString()} available=${j.last_run_availability_time && new Date(j.last_run_availability_time * 1000).toISOString()} interval_h=${j.update_interval_seconds / 3600}`);
  }
})();
