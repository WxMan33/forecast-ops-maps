/* Model-trend archive: shared by the archive builder (Node) and the Guidance updates panel (browser).
   trend-archive-v1.
   - Qualifying cycles are 00Z and 12Z. A source's run is "confirmed" only when the source itself
     identifies it (Open-Meteo Single Runs API: the run requested by run= and a response time axis that
     starts at that cycle; NOAA native feeds: the GRIB initialization). Anything else is a timestamped
     snapshot and is never called "00Z" or "12Z".
   - Comparisons are per source: a source's newest archived run against the same source's run 12, 24
     or 48 h earlier, over IDENTICAL valid windows (same local calendar day, same hours) with the same
     member coverage; precipitation compared as amounts, timing per source, rain rounds kept separate.
   - Plain-language lines summarize agreement across sources; every number behind a line is kept. */
(function (root) {
  "use strict";
  const VERSION = "trend-archive-v1", H = 3600e3;
  const finite = (x) => typeof x === "number" && Number.isFinite(x);
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const median = (a) => { if (!a.length) return null; const s = a.slice().sort((x, y) => x - y), m = Math.floor(s.length / 2); return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

  /* The n most recent 00Z/12Z cycles at or before nowMs (newest first). */
  function cycles(nowMs, n = 5) { const t = Math.floor(nowMs / (12 * H)) * 12 * H; return Array.from({ length: n }, (_, k) => t - k * 12 * H); }
  const cycleId = (ms) => new Date(ms).toISOString().slice(0, 13).replace(/[-T]/g, "");
  const cycleLabel = (ms) => `${new Date(ms).toISOString().slice(11, 13)}Z ${new Date(ms).toLocaleDateString("en-US", { timeZone: "UTC", month: "short", day: "numeric" })}`;

  /* For each source: its newest archived confirmed run, and the run `lagH` hours before that. */
  function pairs(index, lagH) {
    const out = [];
    const bySource = {};
    (index.cycles || []).forEach((c) => Object.entries(c.sources || {}).forEach(([id, s]) => { if (s.identity === "confirmed-run") (bySource[id] = bySource[id] || []).push({ cycle: Date.parse(c.cycle), file: s.file, retrievedAt: s.retrievedAt }); }));
    Object.entries(bySource).forEach(([id, list]) => {
      list.sort((a, b) => b.cycle - a.cycle);
      const cur = list[0], ref = list.find((x) => x.cycle === cur.cycle - lagH * H);
      out.push({ id, cur, ref: ref || null, why: ref ? null : `no archived ${lagH}-h-earlier run` });
    });
    return out;
  }

  /* Wording thresholds: below the first value nothing is said; then "slightly", plain, "sharply". */
  const WORD = { hi: [1, 3, 6], lo: [1, 3, 6], qpf: [0.03, 0.1, 0.35], sn: [0.2, 0.5, 2] };
  const NAME = { hi: "high", lo: "low", qpf: "precipitation", sn: "model snowfall" };
  function words(field, d) { const [a, b, c] = WORD[field], x = Math.abs(d); return x < a ? null : x < b ? "slightly" : x < c ? "" : "sharply"; }
  const fmt = (field, v) => (field === "qpf" ? `${v.toFixed(2)}″` : field === "sn" ? `${v.toFixed(1)}″` : `${v.toFixed(0)}°F`);
  const sgn = (field, v) => (v > 0 ? "+" : v < 0 ? "−" : "±") + fmt(field, Math.abs(v)).replace("°F", "°");

  /* rows of one source in two runs -> per-day comparable values (identical windows and coverage only) */
  function compareSource(curRows, refRows) {
    const out = [];
    Object.entries(curRows || {}).forEach(([day, r]) => {
      const p = refRows && refRows[day]; if (!p || !same(r.win, p.win)) return;
      const item = { day, fields: {}, timing: null, limited: [] };
      ["hi", "lo", "qpf", "sn"].forEach((f) => {
        if (!finite(r[f]) || !finite(p[f])) return;
        if (r.support && p.support && !same(r.support[f], p.support[f])) { item.limited.push(`${f}: member coverage changed`); return; }
        if (f === "qpf" && (r.prorated || p.prorated)) { item.limited.push("precipitation: interval timing limits the comparison"); return; }
        item.fields[f] = { from: p[f], to: r[f], delta: r[f] - p[f] };
      });
      const a = p.timing, b = r.timing, ok = (t) => t && ["wet", "quiet"].includes(t.status) && !t.members;
      if (ok(a) && ok(b)) item.timing = { from: a, to: b };
      out.push(item);
    });
    return out;
  }

  /* Plain-language lines across sources. perSource: [{id, items: compareSource(...)}] */
  function summarize(perSource, opts = {}) {
    const minSources = opts.minSources || 3, agree = opts.agree || 2 / 3, lines = [], days = new Set();
    perSource.forEach((s) => s.items.forEach((i) => days.add(i.day)));
    [...days].sort().forEach((day) => {
      const at = perSource.map((s) => ({ id: s.id, it: s.items.find((i) => i.day === day) })).filter((x) => x.it);
      ["qpf", "hi", "lo", "sn"].forEach((f) => {
        const v = at.filter((x) => x.it.fields[f]).map((x) => ({ id: x.id, ...x.it.fields[f] }));
        if (v.length < minSources) return;
        const thr = WORD[f][0], up = v.filter((x) => x.delta >= thr), down = v.filter((x) => x.delta <= -thr), m = median(v.map((x) => x.delta));
        if (f === "sn" && v.every((x) => Math.max(x.from, x.to) < 0.1)) return;
        if (f === "qpf" && v.every((x) => Math.max(x.from, x.to) < 0.01)) return;
        const dir = up.length >= agree * v.length ? 1 : down.length >= agree * v.length ? -1 : 0;
        if (dir && words(f, m) !== null) {
          const w = words(f, m), n = dir > 0 ? up.length : down.length;
          const text = f === "qpf" ? `Precipitation estimates have ${dir > 0 ? "risen" : "fallen"}${w ? " " + w : ""}`
            : f === "sn" ? `Model snowfall has ${dir > 0 ? "risen" : "fallen"}${w ? " " + w : ""}`
            : `The ${NAME[f]} is trending${w ? " " + w : ""} ${dir > 0 ? "warmer" : "cooler"}`;
          lines.push({ day, kind: f, dir, text: `${text} (median ${sgn(f, m)}, ${n} of ${v.length} models)`, median: m, n: v.length, agreeing: n, sources: v });
        } else if (up.length && down.length && Math.min(up.length, down.length) >= Math.max(2, v.length / 3) && Math.max(...v.map((x) => Math.abs(x.delta))) >= WORD[f][1]) {
          lines.push({ day, kind: f, dir: 0, text: `Models disagree on the ${NAME[f]} trend: ${up.length} up, ${down.length} down`, n: v.length, sources: v });
        }
      });
      /* timing: rain signal and first-round onset / last-round end, per source; rounds stay separate */
      const t = at.filter((x) => x.it.timing).map((x) => ({ id: x.id, from: x.it.timing.from, to: x.it.timing.to }));
      if (t.length >= minSources) {
        const wetBefore = t.filter((x) => x.from.status === "wet").length, wetNow = t.filter((x) => x.to.status === "wet").length;
        if (Math.abs(wetNow - wetBefore) >= Math.max(2, Math.ceil(t.length / 4))) lines.push({ day, kind: "signal", dir: Math.sign(wetNow - wetBefore), text: `${wetNow > wetBefore ? "More" : "Fewer"} models now show precipitation (${wetBefore} → ${wetNow} of ${t.length})`, n: t.length, sources: t.map((x) => ({ id: x.id, from: x.from.status, to: x.to.status })) });
        [["onset", "onsetClipped", "arrival"], ["end", "endClipped", "end"]].forEach(([k, clip, label]) => {
          const v = t.filter((x) => x.from.status === "wet" && x.to.status === "wet" && !x.from[clip] && !x.to[clip] && finite(x.from[k]) && finite(x.to[k])).map((x) => ({ id: x.id, from: x.from[k], to: x.to[k], delta: (x.to[k] - x.from[k]) / H }));
          if (v.length < minSources) return;
          const m = median(v.map((x) => x.delta)), early = v.filter((x) => x.delta <= -2), late = v.filter((x) => x.delta >= 2);
          const dir = late.length >= agree * v.length ? 1 : early.length >= agree * v.length ? -1 : 0;
          if (dir && Math.abs(m) >= 2) lines.push({ day, kind: k, dir, text: `Models now hint that precipitation will ${label === "arrival" ? "arrive" : "end"} ${dir < 0 ? "earlier" : "later"} (median ${Math.abs(m).toFixed(0)} h, ${dir < 0 ? early.length : late.length} of ${v.length} models; ${label === "arrival" ? "first" : "last"} hour with ≥0.01″)`, median: m, n: v.length, sources: v });
        });
        const rounds = t.filter((x) => x.from.status === "wet" && x.to.status === "wet").map((x) => ({ id: x.id, from: (x.from.rounds || []).length, to: (x.to.rounds || []).length }));
        const split = rounds.filter((x) => x.from <= 1 && x.to >= 2).length, merged = rounds.filter((x) => x.from >= 2 && x.to <= 1).length;
        if (rounds.length >= minSources && split >= agree * rounds.length) lines.push({ day, kind: "rounds", dir: 1, text: `Most models now split the precipitation into separate rounds (${split} of ${rounds.length})`, n: rounds.length, sources: rounds });
        if (rounds.length >= minSources && merged >= agree * rounds.length) lines.push({ day, kind: "rounds", dir: -1, text: `Most models now show one continuous period of precipitation instead of separate rounds (${merged} of ${rounds.length})`, n: rounds.length, sources: rounds });
      }
    });
    return lines;
  }

  /* Compare a whole archive at one lag: per-source runs in, plain-language lines out. */
  function trend(index, files, lagH, opts) {
    const ps = pairs(index, lagH), perSource = [], skipped = [];
    ps.forEach((p) => {
      if (!p.ref) { skipped.push({ id: p.id, why: p.why }); return; }
      const a = files[p.cur.file], b = files[p.ref.file];
      if (!a || !b) { skipped.push({ id: p.id, why: "archive file unavailable" }); return; }
      if (a.tz !== b.tz || !same(a.loc, b.loc) || a.method !== b.method) { skipped.push({ id: p.id, why: "different location or method" }); return; }
      perSource.push({ id: p.id, cur: p.cur.cycle, ref: p.ref.cycle, items: compareSource(a.rows, b.rows) });
    });
    return { lagH, lines: summarize(perSource, opts), perSource, skipped };
  }

  const api = { VERSION, cycles, cycleId, cycleLabel, pairs, compareSource, summarize, trend, WORD };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.TrendArchive = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
