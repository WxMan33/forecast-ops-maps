#!/usr/bin/env node
/* CVG model-trend archive (trend-archive-v1). Builds data/trends/<LOC>/ inside the site being deployed.
   Uses the SAME loaders and snapshot code as the forecast site (vendored from WxMan33/forecast-ops, see
   vendor/VENDOR.json), so archived rows are computed exactly like the browser's Guidance updates.

   Run identity:
     confirmed-run  deterministic models from the Open-Meteo Single Runs API: requested with run=<cycle>,
                    accepted only if the response's own time axis starts at that cycle, and only once
                    Open-Meteo's metadata shows that run (or a later one) fully available
     snapshot       ensembles and NWS: no run-specific retrieval exists, so they are archived as
                    timestamped snapshots (provider-reported latest run / NWS update time recorded, never
                    called 00Z or 12Z)
   Retention: the five most recent qualifying 00Z/12Z cycles per source (and five ensemble/NWS
   snapshots). Files are immutable once written; earlier files are carried forward from the live site
   with SHA-256 checks. */
"use strict";
const fs = require("fs"), path = require("path"), crypto = require("crypto");
const V = path.join(__dirname, "vendor");
global.window = undefined;
const C = require(path.join(V, "ops-core.js")), D = require(path.join(V, "ops-data.js")), MT = require(path.join(V, "model-trends.js")), TA = require(path.join(V, "trend-archive-core.js"));
const arg = (k, d) => { const i = process.argv.indexOf("--" + k); return i > 0 ? process.argv[i + 1] : d; };
const SITE = path.resolve(arg("site", "_site")), PUBLISHED = arg("published", "https://wxman33.github.io/forecast-ops-maps"), NOW = arg("now") ? Date.parse(arg("now")) : Date.now();
const LOCS = { CVG: { id: "CVG", name: "CVG airport, Cincinnati/Northern Kentucky", lat: 39.04456, lon: -84.67229 } };
const LOC = LOCS[arg("loc", "CVG")];
const SINGLE = "https://single-runs-api.open-meteo.com/v1/forecast", KEEP = 5, DAYS = 8, H = 3600e3;
const rel = (p) => `data/trends/${LOC.id}/${p}`;
const sha = (b) => crypto.createHash("sha256").update(b).digest("hex");

async function http(url, asJSON = true) {
  const r = await fetch(url, { signal: AbortSignal.timeout(60000), headers: { "User-Agent": "forecast-ops trend archive (github.com/WxMan33/forecast-ops-maps)" } });
  if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
  return asJSON ? r.json() : Buffer.from(await r.arrayBuffer());
}
function write(p, obj) { const full = path.join(SITE, rel(p)), buf = Buffer.from(JSON.stringify(obj)); fs.mkdirSync(path.dirname(full), { recursive: true }); fs.writeFileSync(full, buf); return { file: p, sha256: sha(buf), bytes: buf.length }; }

/* one source's rows from a loaded data object, in the browser's model-trends format */
function sourceSnapshot(data, id, at) {
  const snap = MT.capture(C, data, LOC, at), m = snap.models[id];
  return m ? { version: snap.version, method: snap.method, at: snap.at, tz: snap.tz, loc: snap.loc, kind: m.kind, rows: m.rows } : null;
}

async function confirmedRun(id, cycleMs) {
  const run = new Date(cycleMs).toISOString().slice(0, 16);
  const spec = { ...C.config.blend, base: SINGLE, params: { ...C.config.blend.params, run } };
  const res = await D.loadModels(spec, { lat: LOC.lat, lon: LOC.lon, days: DAYS, ids: [id] });
  const md = res.models && res.models[id];
  if (!md) return { error: (res.problems || []).join("; ") || "no data" };
  if (md.provenance) return { error: "response came from a native feed fallback, not the requested run" };
  if (res.ms[0] !== cycleMs) return { error: `response starts ${new Date(res.ms[0]).toISOString()}, not at the requested run ${run}Z` };
  const s = sourceSnapshot(res, id, cycleMs); if (!s || !Object.keys(s.rows).length) return { error: "no complete local days in this run" };
  return { snap: { ...s, identity: { type: "confirmed-run", cycle: new Date(cycleMs).toISOString(), how: `Open-Meteo Single Runs API, run=${run}; response time axis starts at the run`, retrievedAt: md.retrievedAt || new Date().toISOString() } } };
}

async function main() {
  fs.mkdirSync(path.join(SITE, rel("")), { recursive: true });
  let prev = { cycles: [], snapshots: [] };
  try { prev = await http(`${PUBLISHED}/${rel("index.json")}`); } catch (e) { console.log("no published trend index yet:", e.message); }
  const keep = TA.cycles(NOW, KEEP), keepIds = new Set(keep.map(TA.cycleId));
  /* provider metadata: is a run (or later) fully available? */
  const meta = {};
  for (const [id, mid] of Object.entries(C.config.metaIds)) { try { const j = await http(`https://api.open-meteo.com/data/${mid}/static/meta.json`); meta[id] = { init: j.last_run_initialisation_time * 1000, avail: j.last_run_availability_time * 1000 }; } catch (e) { meta[id] = null; } }
  const det = C.config.blend.models.map((m) => m.id);
  const index = { schema: 1, version: TA.VERSION, loc: LOC, generatedAt: new Date(NOW).toISOString(), retention: `${KEEP} most recent 00Z/12Z cycles per source; ${KEEP} ensemble/NWS snapshots`, cycles: [], snapshots: [], notes: [] };
  const carried = new Set(); let added = 0;
  async function carry(entry) { const b = await http(`${PUBLISHED}/${rel(entry.file)}`, false); if (sha(b) !== entry.sha256) throw new Error(`hash mismatch ${entry.file}`); const full = path.join(SITE, rel(entry.file)); fs.mkdirSync(path.dirname(full), { recursive: true }); fs.writeFileSync(full, b); carried.add(entry.file); }
  for (const cyc of keep) {
    const cid = TA.cycleId(cyc), old = (prev.cycles || []).find((c) => c.id === cid), entry = { id: cid, cycle: new Date(cyc).toISOString(), sources: {} };
    for (const id of det) {
      const o = old && old.sources[id];
      if (o && o.identity === "confirmed-run") { try { await carry(o); entry.sources[id] = o; continue; } catch (e) { index.notes.push(`${cid} ${id}: carry failed (${e.message}); refetching`); } }
      const m = meta[id];
      if (m && m.init < cyc) { entry.sources[id] = { identity: "pending", why: "provider has not finished this run yet" }; continue; }
      if (!m && NOW - cyc < 10 * H) { entry.sources[id] = { identity: "pending", why: "no provider metadata; waiting 10 h after the run time before archiving" }; continue; }
      try {
        const r = await confirmedRun(id, cyc);
        if (r.error) { entry.sources[id] = { identity: "unavailable", why: r.error }; continue; }
        entry.sources[id] = { identity: "confirmed-run", retrievedAt: r.snap.identity.retrievedAt, ...write(`runs/${cid}/${id}.json`, r.snap) }; added++;
      } catch (e) { entry.sources[id] = { identity: "unavailable", why: String(e.message || e).slice(0, 200) }; }
    }
    index.cycles.push(entry);
  }
  /* ensembles and NWS: timestamped snapshots, taken when the provider reports a qualifying cycle not yet snapshotted */
  const ensIds = C.config.blendEns.models.map((m) => m.source), reported = {};
  C.config.blendEns.models.forEach((m) => { const mm = meta[m.source]; if (mm && keepIds.has(TA.cycleId(mm.init)) && new Date(mm.init).getUTCHours() % 12 === 0) reported[m.source] = mm.init; });
  const prevSnaps = (prev.snapshots || []).filter((s) => Date.parse(s.retrievedAt) > NOW - 3 * 86400e3);
  const already = new Set(prevSnaps.flatMap((s) => Object.entries(s.sources).map(([id, x]) => `${id}|${x.reportedCycle}`)));
  const want = Object.entries(reported).filter(([id, c]) => !already.has(`${id}|${new Date(c).toISOString()}`));
  for (const s of prevSnaps.slice(0, KEEP)) { try { for (const x of Object.values(s.sources)) await carry(x); index.snapshots.push(s); } catch (e) { index.notes.push(`snapshot ${s.retrievedAt}: carry failed (${e.message})`); } }
  if (want.length || (!prevSnaps.length)) {
    const at = NOW, sid = new Date(at).toISOString().slice(0, 16).replace(/[-:T]/g, ""), snapEntry = { id: sid, retrievedAt: new Date(at).toISOString(), sources: {} };
    try {
      const en = await D.loadEnsemble(C.config.blendEns, { lat: LOC.lat, lon: LOC.lon, days: DAYS });
      const nws = await D.loadNWS({ lat: LOC.lat, lon: LOC.lon }).catch(() => null);
      if (!en.empty) {
        const data = { times: en.times, ms: en.ms, tz: en.tz, offset: en.offset, dtimes: [], models: {} };
        C.blend.addSources(data, en, nws && nws.ok ? C.nws.onAxis(nws, data.ms) : null);
        for (const id of [...ensIds, "nws"]) {
          const s = data.models[id] && sourceSnapshot(data, id, at); if (!s) continue;
          const ident = id === "nws" ? { type: "snapshot", nwsUpdated: nws && nws.status && nws.status.updated && nws.status.updated.grid ? new Date(nws.status.updated.grid).toISOString() : null, note: "NWS forecast issuance, not a model run" }
            : { type: "snapshot", reportedCycle: reported[id] ? new Date(reported[id]).toISOString() : null, note: "run not confirmed: Open-Meteo responses do not state their run; provider metadata reported this run as latest at retrieval" };
          snapEntry.sources[id] = { identity: "snapshot", reportedCycle: ident.reportedCycle || null, nwsUpdated: ident.nwsUpdated || null, retrievedAt: snapEntry.retrievedAt, ...write(`snapshots/${sid}/${id}.json`, { ...s, identity: ident }) };
        }
        if (Object.keys(snapEntry.sources).length) { index.snapshots.unshift(snapEntry); added++; }
      }
    } catch (e) { index.notes.push("ensemble/NWS snapshot failed: " + String(e.message || e).slice(0, 200)); }
  }
  index.snapshots = index.snapshots.slice(0, KEEP);
  const okRuns = index.cycles.reduce((n, c) => n + Object.values(c.sources).filter((s) => s.identity === "confirmed-run").length, 0);
  if (!okRuns && !(prev.cycles || []).length) throw new Error("no confirmed runs archived and nothing published before");
  write("index.json", index);
  const changed = added > 0 || JSON.stringify((prev.cycles || []).map((c) => c.id)) !== JSON.stringify(index.cycles.map((c) => c.id));
  const out = arg("changed-output"); if (out) fs.appendFileSync(out, `trends_changed=${changed}\n`);
  console.log(`trends ${LOC.id}: ${okRuns} confirmed source-runs in ${index.cycles.length} cycles (${added} new, ${carried.size} carried), ${index.snapshots.length} snapshots; ${changed ? "changed" : "unchanged"}`);
  index.cycles.forEach((c) => console.log(" ", c.id, Object.entries(c.sources).map(([id, s]) => `${id}:${s.identity === "confirmed-run" ? "ok" : s.identity}`).join(" ")));
  if (index.notes.length) console.log("notes:", index.notes.join(" | "));
}
/* --carry-only: republish the previous archive unchanged (used when a build fails, so a failed trend
   update never removes the archive from the deployed site or blocks the map deployment) */
async function carryOnly() {
  const prev = await http(`${PUBLISHED}/${rel("index.json")}`);
  const files = [...(prev.cycles || []).flatMap((c) => Object.values(c.sources).filter((x) => x.file)), ...(prev.snapshots || []).flatMap((s) => Object.values(s.sources))];
  for (const f of files) { const b = await http(`${PUBLISHED}/${rel(f.file)}`, false); if (sha(b) !== f.sha256) throw new Error(`hash mismatch ${f.file}`); const full = path.join(SITE, rel(f.file)); fs.mkdirSync(path.dirname(full), { recursive: true }); fs.writeFileSync(full, b); }
  write("index.json", { ...prev, notes: [...(prev.notes || []), `update failed at ${new Date(NOW).toISOString()}; previous archive republished unchanged`] });
  console.log(`trends: previous archive republished (${files.length} files)`);
}
(process.argv.includes("--carry-only") ? carryOnly() : main()).catch((e) => { console.error(e); process.exit(1); });
