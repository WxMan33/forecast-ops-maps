/* Archive builder self-test with synthetic Open-Meteo look-alike responses (archive/test/mock-fetch.js). */
"use strict";
const assert = require("assert"), cp = require("child_process"), fs = require("fs"), os = require("os"), path = require("path");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "trends-")), script = path.join(__dirname, "..", "trends.js"), mock = path.join(__dirname, "mock-fetch.js");
const run = (args, env = {}) => { try { return { ok: true, out: cp.execFileSync("node", ["-r", mock, script, ...args], { env: { ...process.env, ...env }, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }) }; } catch (e) { return { ok: false, out: String(e.stdout) + String(e.stderr) }; } };
const idx = (d) => JSON.parse(fs.readFileSync(path.join(d, "data/trends/CVG/index.json"), "utf8"));
let n = 0; const ok = (c, m) => { assert(c, m); n++; };
try {
  const s1 = path.join(root, "s1"), r1 = run(["--site", s1, "--published", "https://published.test", "--now", "2026-11-04T20:00:00Z"]);
  ok(r1.ok, r1.out); const i1 = idx(s1);
  ok(i1.cycles.length === 5 && i1.cycles[0].id === "2026110412", "five qualifying cycles, newest 12Z");
  ok(Object.values(i1.cycles[0].sources).filter((s) => s.identity === "confirmed-run").length >= 9, "deterministic sources archived as confirmed runs");
  const f = JSON.parse(fs.readFileSync(path.join(s1, "data/trends/CVG", i1.cycles[0].sources.gfs_seamless.file)));
  ok(f.identity.type === "confirmed-run" && f.identity.cycle === "2026-11-04T12:00:00.000Z" && Object.keys(f.rows).length >= 5, "run file carries confirmed identity and daily rows");
  ok(i1.snapshots.length === 1 && Object.values(i1.snapshots[0].sources).every((x) => x.identity === "snapshot"), "ensembles archived only as snapshots");
  const ens = Object.values(i1.snapshots[0].sources)[0]; const ef = JSON.parse(fs.readFileSync(path.join(s1, "data/trends/CVG", ens.file)));
  ok(ef.identity.type === "snapshot" && /not confirmed/.test(ef.identity.note), "snapshot limitation is recorded with the data");
  const rs = run(["--site", path.join(root, "sh"), "--published", "https://published.test", "--now", "2026-11-04T20:00:00Z"], { MOCK_MODE: "shifted" });
  ok(!rs.ok && /no confirmed runs/.test(rs.out), "responses that do not start at the requested run are rejected");
  const s2 = path.join(root, "s2"), r2 = run(["--site", s2, "--published", "https://published.test", "--now", "2026-11-05T08:00:00Z"], { MOCK_PUBLISHED: s1, MOCK_NOW: "2026-11-05T08:00:00Z" });
  ok(r2.ok && /carried/.test(r2.out), r2.out); const i2 = idx(s2);
  ok(i2.cycles[0].id === "2026110500" && !i2.cycles.some((c) => c.id === "2026110212"), "a new cycle is added and the sixth-oldest dropped");
  ok(/meteofrance_seamless:pending/.test(r2.out), "a source without provider metadata waits before archiving a recent run");
  fs.writeFileSync(path.join(s1, "data/trends/CVG/runs/2026110412/gfs_seamless.json"), "{}");
  const r3 = run(["--site", path.join(root, "s3"), "--published", "https://published.test", "--now", "2026-11-04T20:00:00Z"], { MOCK_PUBLISHED: s1 });
  ok(r3.ok && /hash mismatch/.test(r3.out), "a changed published file is detected and refetched, not carried");
  const r4 = run(["--carry-only", "--site", path.join(root, "s4"), "--published", "https://published.test"], { MOCK_PUBLISHED: s2 });
  ok(r4.ok && /republished/.test(r4.out), "carry-only republishes the previous archive unchanged");
} finally { fs.rmSync(root, { recursive: true, force: true }); }
console.log(`ok - ${n} trend-archive builder checks passed (synthetic responses)`);
