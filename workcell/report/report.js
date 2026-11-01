(function () {
  "use strict";

  const D = JSON.parse(document.getElementById("workcell-data").textContent);
  const $ = (sel, el) => (el || document).querySelector(sel);
  const $$ = (sel, el) => Array.from((el || document).querySelectorAll(sel));
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = s => String(s).replace(/[&<>"']/g, c => ESC[c]);
  const attr = s => (window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/["\\]/g, "\\$&"));

  // ---------------------------------------------------------------- lookups
  const TYPE_ORDER = ["storage", "hotel", "arm", "handoff", "barcode_scanner", "head_stamper", "thermocycler"];
  const TYPE_COLOR = {
    arm: "#e08a2e", storage: "#7d9440", hotel: "#8b6cc4",
    barcode_scanner: "#c05299", head_stamper: "#3b7dd8", thermocycler: "#1f9e89",
    liquid_handler: "#d4a72c", heated_shaker: "#c9503e", plate_sealer: "#5f7a8a",
    plate_peeler: "#8a9aa5", centrifuge: "#6d5bd0", plate_reader: "#2a9bc4",
    vacuum_concentrator: "#9c6b3f", handoff: "#b07cc6",
  };
  const TYPE_LABEL = {
    arm: "arm", storage: "storage", hotel: "hotel", barcode_scanner: "scanner",
    head_stamper: "stamper", thermocycler: "thermocycler",
    liquid_handler: "liquid handler", heated_shaker: "shaker", plate_sealer: "sealer",
    plate_peeler: "peeler", centrifuge: "centrifuge", plate_reader: "plate reader",
    vacuum_concentrator: "concentrator", handoff: "handoff",
  };
  // Badges say what happened to the run; the planner's own status lives in the tooltip and stats table.
  const STATUS_LABEL = {
    complete: "done", optimal: "done · optimal", feasible: "done",
    deadlock: "deadlock", no_solution: "no plan found", infeasible: "no plan possible", alarm: "stopped: alarm",
    incomplete: "incomplete",
  };
  const STATUS_HINT = {
    complete: "All workflows finished. Greedy planners make no claim about how good the plan is.",
    optimal: "All workflows finished, and CP-SAT proved no shorter plan exists.",
    feasible: "All workflows finished. CP-SAT found this plan but hit its time limit before proving it is the best.",
    deadlock: "Planning stopped: workflows ended up waiting on each other and nothing could move.",
    no_solution: "CP-SAT found no plan within its time limit.",
    infeasible: "CP-SAT proved no valid plan exists.",
    alarm: "Execution stopped on an instrument alarm.",
    incomplete: "Some scheduled steps never finished.",
  };
  const SOLVER_LABEL = {
    complete: "— (greedy)", optimal: "optimal (proven best)", feasible: "feasible (not proven best)",
    deadlock: "deadlock", no_solution: "no solution", infeasible: "infeasible", alarm: "alarm",
    incomplete: "incomplete",
  };
  const FINISHED = new Set(["complete", "optimal", "feasible"]);
  // Live (v2) reports: times are seconds, shown as h:mm:ss; v1 reports keep "min".
  const LIVE = D.live;
  const SPEEDS = LIVE ? [1, 2, 5, 10, 30, 60, 120, 300, 600] : [0.5, 1, 2, 5, 10, 20, 50, 100, 200];
  const hms = v => {
    const x = Math.max(0, Math.round(v)), h = Math.floor(x / 3600), m = Math.floor(x % 3600 / 60);
    return `${h}:${String(m).padStart(2, "0")}:${String(x % 60).padStart(2, "0")}`;
  };
  const mss = v => { const x = Math.round(Math.abs(v)); return `${Math.floor(x / 60)}:${String(x % 60).padStart(2, "0")}`; };

  const instById = Object.fromEntries(D.instruments.map(i => [i.id, i]));
  const typeRank = t => { const r = TYPE_ORDER.indexOf(t); return r < 0 ? 99 : r; };
  const instSorted = D.instruments.map((inst, i) => [inst, i])
    .sort((a, b) => typeRank(a[0].type) - typeRank(b[0].type) || a[1] - b[1]).map(x => x[0]);
  const instType = id => (instById[id] ? instById[id].type : "");
  const instColor = id => TYPE_COLOR[instType(id)] || "#888";

  const wfIndex = Object.fromEntries(D.workflows.map((w, i) => [w.id, i]));
  const hue = i => (i * 137.508 + 210) % 360;
  const wfColor = i => `hsl(${hue(i).toFixed(1)} 62% 52%)`;
  const wfDarkInk = i => { const h = hue(i); return h > 40 && h < 195; };
  const plateOwner = {};
  const plateBorrowers = {};
  D.workflows.forEach((w, i) => {
    w.owned.forEach(p => { plateOwner[p] = i; });
    w.borrowed.forEach(p => { (plateBorrowers[p] = plateBorrowers[p] || []).push(w.id); });
  });
  const compiledById = {};
  const blockWf = {};
  D.compiled.forEach(c => c.blocks.forEach(b => { compiledById[b.id] = b; blockWf[b.id] = c.workflow; }));
  const baseId = id => id.replace(/\.park$/, "");
  const wfOf = id => blockWf[baseId(id)] || id.slice(0, id.lastIndexOf("/"));
  const statusOf = run => run.stats.status;

  function primaryType(b) {
    const ins = b.instructions || [];
    if (ins.some(i => i.op === "move" && instType(i.instrument) === "arm")) return "arm";
    const real = ins.find(i => i.duration > 0) || ins[0];
    return real ? instType(real.instrument) : instType(b.instruments[0]);
  }

  // ---------------------------------------------------------------- formatting
  function fmtVal(v) {
    if (Array.isArray(v)) {
      if (v.length > 4) return `[${v.length} steps]`;
      return "(" + v.map(fmtVal).join(", ") + ")";
    }
    if (v && typeof v === "object") return "{…}";
    return String(v);
  }
  function fmtInstr(i) {
    const args = Object.entries(i.args || {}).map(([k, v]) => `${esc(k)}=${esc(fmtVal(v))}`).join(", ");
    return `<li class="${i.duration ? "" : "zero"}">${esc(i.instrument)}.<span class="op">${esc(i.op)}</span>(${args}) <span class="d">${LIVE ? hms(i.duration) : `${i.duration}m`}</span></li>`;
  }
  const fmtNum = (v, d) => (v === null || v === undefined ? "—" : (d ? Number(v).toFixed(d) : String(v)));
  const dur = (v, d) => (v === null || v === undefined ? "—" : LIVE ? hms(v) : `${fmtNum(v, d)} min`);
  const at = v => (LIVE ? hms(v) : String(v));
  const badge = s => `<span class="badge ${esc(s)}" title="${esc(STATUS_HINT[s] || s)}">${esc(STATUS_LABEL[s] || s)}</span>`;
  const pct = (x, T) => (100 * x / T) + "%";
  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-9))));
    for (const m of [1, 2, 5, 10]) if (m * p >= raw) return Math.max(1, m * p);
    return 10 * p;
  }

  // ---------------------------------------------------------------- per-run prep (cached)
  function lastAtOrBefore(arr, key, t) {   // arr sorted by key; index of last item with item[key] <= t
    let lo = 0, hi = arr.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (arr[mid][key] <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  }

  function prep(run) {
    if (run._p) return run._p;
    const byId = {};
    let T = run.stats.makespan || 0;
    run.blocks.forEach(b => { byId[b.id] = b; T = Math.max(T, b.end); });
    const ghosts = [];
    const cycle = new Set(run.deadlock ? run.deadlock.cycle || [] : []);
    if (run.deadlock) {
      const t0 = run.deadlock.time;
      run.deadlock.waits.forEach(w => {
        const c = compiledById[baseId(w.block)];
        const dur = Math.max(1, c ? c.duration : 1);
        const g = { id: w.block, workflow: wfOf(w.block), kind: c ? c.kind : "", label: c ? c.label : w.block,
          start: t0, end: t0 + dur, reason: w.reason, ghost: true, cycle: cycle.has(w.block), instructions: c ? c.instructions : [],
          instruments: c ? c.instruments : [], plates: c ? c.plates : [] };
        ghosts.push(g); byId[g.id] = byId[g.id] || g;
        T = Math.max(T, g.end);
      });
      T = Math.max(T, t0);
    }
    if (run.alarm) T = Math.max(T, run.alarm.time);
    T = Math.max(T, 1);
    const plateMoves = {};
    run.moves.forEach(m => { (plateMoves[m.plate] = plateMoves[m.plate] || []).push(m); });
    Object.values(plateMoves).forEach(a => a.sort((x, y) => x.time - y.time));
    const instBlocks = {};
    run.blocks.forEach(b => b.instruments.forEach(i => { (instBlocks[i] = instBlocks[i] || []).push(b); }));
    Object.values(instBlocks).forEach(a => a.sort((x, y) => x.start - y.start));
    const markers = [];
    if (run.deadlock) markers.push({ time: run.deadlock.time, label: `deadlock t=${run.deadlock.time}` });
    if (run.alarm) markers.push({ time: run.alarm.time, label: `alarm t=${at(run.alarm.time)}` });
    return (run._p = { T, byId, ghosts, cycle, plateMoves, instBlocks, markers });
  }

  // ---------------------------------------------------------------- state
  const S = { run: 0, t: 0, playing: false, speed: null, speedTouched: false, zoom: 1, sel: null, cursors: [] };
  const R = () => D.runs[S.run];
  const P = () => prep(R());

  // ---------------------------------------------------------------- header, tabs, stats
  $("#summary").textContent =
    `${D.workflows.length} workflow${D.workflows.length === 1 ? "" : "s"} · ${D.instruments.length} instruments · ${D.runs.length} mode${D.runs.length === 1 ? "" : "s"}`;

  function renderTabs() {
    $("#tabs").hidden = !!LIVE;           // a live report is a single run
    $("#tabs").innerHTML = D.runs.map((r, i) =>
      `<button type="button" class="tab" role="tab" data-run="${i}" aria-selected="${i === S.run}">${esc(r.mode)} ${badge(statusOf(r))}</button>`
    ).join("");
  }
  $("#tabs").addEventListener("click", e => {
    const b = e.target.closest("[data-run]");
    if (b) setRun(+b.dataset.run);
  });

  function renderStats() {
    const runs = D.runs;
    const finished = runs.filter(r => FINISHED.has(statusOf(r)));
    const best = finished.length > 1 ? Math.min(...finished.map(r => r.stats.makespan)) : null;
    const rows = [
      ["status", r => badge(statusOf(r))],
      ["makespan", r => `<span class="${best !== null && FINISHED.has(statusOf(r)) && r.stats.makespan === best ? "best" : ""}">${dur(r.stats.makespan)}</span>`],
      ["completed", r => `${r.stats.completed} / ${r.stats.total}`],
      ["mean latency", r => dur(r.stats.mean_latency, 1)],
      ["max latency", r => dur(r.stats.max_latency)],
      ["mean completion", r => dur(r.stats.mean_completion, 1)],
      ["waiting (total)", r => dur(r.stats.total_waiting)],
      ["waiting (mean)", r => dur(r.stats.mean_waiting, 1)],
      ["blocked (total)", r => dur(Object.values(r.stats.blocked || {}).reduce((a, b) => a + b, 0))],
      ["planner status", r => (LIVE ? "live, dispatched just in time" : esc(SOLVER_LABEL[r.status] || r.status))],
      ["solve time", r => `${Number(r.stats.solve_seconds).toFixed(2)} s`],
      ...(LIVE ? [] : [["lock waits", r => String(r.stats.lock_waits)]]),
      ["alarm", r => (r.stats.alarm ? esc(r.stats.alarm) : "—")],
    ];
    if (LIVE) {
      const L = LIVE.stats;
      rows.push(
        ["seed", () => String(L.seed)],
        ["total delay", () => hms(L.total_delay)],
        ["forecast makespan", () => (L.planned_makespan == null ? "— (deadlock)" : hms(L.planned_makespan))],
        ["actual makespan", () => hms(L.actual_makespan)]);
      if (L.max_idle_hold) rows.push(
        ["idle-hold limit", () => mss(L.max_idle_hold)],
        ["parks", () => String(L.parks)],
        ["idle-hold violations", () => L.idle_violations ? `${L.idle_violations} (worst ${mss(L.worst_idle)})` : "0"]);
    }
    const head = `<thead><tr><th></th>${runs.map((r, i) => `<th data-run="${i}">${esc(r.mode)}</th>`).join("")}</tr></thead>`;
    const body = rows.map(([name, f]) => `<tr><td>${name}</td>${runs.map((r, i) =>
      `<td data-run="${i}" class="${name === "alarm" && r.stats.alarm ? "alarm-msg" : ""}">${f(r)}</td>`).join("")}</tr>`).join("");
    const t = $("#stats-table");
    t.innerHTML = head + `<tbody>${body}</tbody>`;
    t.addEventListener("click", e => {
      const c = e.target.closest("[data-run]");
      if (c) setRun(+c.dataset.run);
    });
  }
  function markStatsActive() {
    $$("#stats-table [data-run]").forEach(c => c.classList.toggle("active", +c.dataset.run === S.run));
  }

  function renderUtil() {
    const run = R();
    const u = run.stats.utilization || {};
    const bl = run.stats.blocked || {};
    const T = run.stats.makespan || 0;
    $("#util").innerHTML = instSorted.map(inst => {
      const v = u[inst.id] || 0;
      const has = inst.id in bl, m = bl[inst.id] || 0, bv = T ? m / T : 0;
      const title = has ? `${dur(m)} holding a plate while idle (blocked)` : "";
      return `<div class="ub" title="${esc(title)}"><span title="${esc(inst.type)}">${esc(inst.id)}</span><div class="rail"><div class="fill" style="width:${(100 * v).toFixed(1)}%;background:${instColor(inst.id)}"></div><div class="fill blocked" style="width:${(100 * bv).toFixed(1)}%;--c:${instColor(inst.id)}"></div></div><span class="pct">${Math.round(100 * v)}%</span><span class="blk-m">${has ? (m ? `${LIVE ? hms(m) : `${m}m`} blocked` : "—") : ""}</span></div>`;
    }).join("");
    $("#util-note").textContent = `${run.mode} · solid: busy time / makespan (${dur(run.stats.makespan)}) · hatched: blocked, a plate rests in the instrument while it is idle`;
  }

  // ---------------------------------------------------------------- alerts
  function renderAlerts() {
    const run = R();
    const parts = [];
    if (run.deadlock) {
      const dl = run.deadlock;
      const cyc = dl.cycle || [];
      const rank = id => { const i = cyc.indexOf(id); return i < 0 ? cyc.length : i; };
      const waits = dl.waits.map((w, i) => [w, i]).sort((a, b) => rank(a[0].block) - rank(b[0].block) || a[1] - b[1]).map(x => x[0]);
      const cycText = cyc.length
        ? `<p class="cycle-line"><b>Wait cycle:</b> ${[...cyc, cyc[0]].map(id => `<span class="blk" data-sel="${esc(id)}">${esc(id)}</span>`).join(" → ")} <span class="muted small">(each waits for something held by the next one's workflow)</span></p>`
        : "";
      parts.push(`<div class="alert"><h3>Deadlock at t=${dl.time}</h3>
        <p>Work remains but nothing is running and nothing can start. ${run.stats.completed}/${run.stats.total} workflows complete. Waiting blocks (dashed red on the workflow Gantt; the wait cycle is solid red and listed first):</p>
        ${cycText}
        <div class="scroll"><table>${waits.map(w => {
          const c = compiledById[baseId(w.block)];
          const inCycle = cyc.includes(w.block);
          return `<tr class="${inCycle ? "cycle" : ""}"><td><span class="blk" data-sel="${esc(w.block)}">${esc(w.block)}</span>${inCycle ? ' <span class="cyc-tag">cycle</span>' : ""}</td><td>${esc(c ? c.label : "")}</td><td class="muted">${esc(w.reason)}</td></tr>`;
        }).join("")}</table></div></div>`);
    }
    if (run.alarm) {
      const a = run.alarm;
      parts.push(`<div class="alert"><h3>Alarm at t=${a.time}</h3><p>${esc(a.message)}</p>
        <table><tr><td class="muted">block</td><td><span class="blk" data-sel="${esc(a.block)}">${esc(a.block)}</span></td></tr>
        <tr><td class="muted">instruction</td><td class="mono">${esc(a.instrument)}.${esc(a.op)}</td></tr></table>
        <p class="muted small">The run halted here; everything after t=${a.time} did not happen.</p></div>`);
    }
    if (run.errors && run.errors.length) {
      parts.push(`<div class="alert errors"><h3>Plan validation errors (${run.errors.length})</h3><ul>${run.errors.map(e => `<li>${esc(e)}</li>`).join("")}</ul></div>`);
    }
    const el = $("#run-alerts");
    el.innerHTML = parts.join("");
    el.hidden = !parts.length;
  }
  $("#run-alerts").addEventListener("click", e => {
    const b = e.target.closest("[data-sel]");
    if (b) select(b.dataset.sel, true, true);
  });

  // ---------------------------------------------------------------- gantt
  function ganttShell(T, rowsHtml) {
    const step = niceStep(T / (8 * S.zoom));
    let ticks = "", grid = "";
    for (let x = 0; x <= T + 1e-9; x += step) {
      ticks += `<span class="tick" style="left:${pct(x, T)}">${at(x)}</span>`;
      if (x > 0) grid += `<div class="gridline" style="left:${pct(x, T)}"></div>`;
    }
    const markers = P().markers.map(m =>
      `<div class="marker ${m.cls || ""}" style="left:${pct(m.time, T)}" title="${esc(m.label)}"><span>${esc(m.cls ? "" : m.label)}</span></div>`).join("");
    return `<div class="g-scroll"><div class="g-inner" style="width:${S.zoom * 100}%">
      <div class="g-axis"><div class="g-label">${LIVE ? "h:mm:ss" : "min"}</div><div class="g-track">${ticks}</div></div>
      ${rowsHtml}
      <div class="g-overlay">${grid}${markers}<div class="cursor"></div></div>
    </div></div>`;
  }
  const row = (label, bars) => `<div class="g-row"><div class="g-label">${label}</div><div class="g-track">${bars}</div></div>`;

  function barHtml(b, color, T, extra) {
    const cls = ["bar"];
    if (b.inserted) cls.push("ins");
    if (b.ghost) cls.push("ghost");
    if (b.cycle) cls.push("cycle");
    const run = R();
    if (run.alarm && run.alarm.block === b.id) cls.push("alarm");
    if (extra) cls.push(extra);
    const bg = b.ghost ? "" : `background:${color};`;
    return `<div class="${cls.join(" ")}" data-b="${esc(b.id)}" style="left:${pct(b.start, T)};width:${pct(Math.max(b.end - b.start, 0), T)};${bg}"></div>`;
  }

  function renderGantts() {
    const run = R(), p = P(), T = p.T;
    const byInst = instSorted.map(inst => {
      const bars = (p.instBlocks[inst.id] || []).map(b => barHtml(b, wfColor(wfIndex[b.workflow] || 0), T)).join("");
      return row(`<span class="sw" style="background:${instColor(inst.id)}"></span>${esc(inst.id)}<span class="sub">${esc(TYPE_LABEL[inst.type] || inst.type)}</span>`, bars);
    }).join("");
    $("#gantt-inst").innerHTML = ganttShell(T, byInst);

    const perWf = {};
    run.blocks.forEach(b => { (perWf[b.workflow] = perWf[b.workflow] || []).push(b); });
    const ghostsWf = {};
    p.ghosts.forEach(g => { (ghostsWf[g.workflow] = ghostsWf[g.workflow] || []).push(g); });
    const byWf = D.workflows.map((w, i) => {
      const mine = perWf[w.id] || [];
      let bars = "";
      if (mine.length) {
        const s = Math.min(...mine.map(b => b.start)), e = Math.max(...mine.map(b => b.end));
        bars += `<div class="bar waitspan" style="left:${pct(s, T)};width:${pct(e - s, T)}"></div>`;
      }
      bars += mine.map(b => barHtml(b, TYPE_COLOR[primaryType(b)] || "#888", T)).join("");
      bars += (ghostsWf[w.id] || []).map(g => barHtml(g, "", T)).join("");
      const lat = run.stats.latency ? run.stats.latency[w.id] : null;
      return row(`<span class="sw" style="background:${wfColor(i)}"></span>${esc(w.id)}<span class="sub">${lat === null || lat === undefined ? "—" : (LIVE ? hms(lat) : lat + "m")}</span>`, bars);
    }).join("");
    $("#gantt-wf").innerHTML = ganttShell(T, byWf);

    S.cursors = $$(".gantt .cursor");
    applySelection(false);
  }

  function renderLegends() {
    const wfs = D.workflows;
    const wfPart = wfs.length <= 12
      ? wfs.map((w, i) => `<span><i style="background:${wfColor(i)}"></i>${esc(w.id)}</span>`).join("")
      : `<span><i style="background:linear-gradient(90deg,${[0, 1, 2, 3, 4].map(wfColor).join(",")})"></i>colored by workflow (${wfs.length})</span>`;
    $("#legend-inst").innerHTML = wfPart + `<span><i class="hatch"></i>planner-inserted</span>`;
    const types = [...new Set(D.instruments.map(i => i.type))].sort((a, b) => typeRank(a) - typeRank(b));
    $("#legend-wf").innerHTML = types.map(t => `<span><i style="background:${TYPE_COLOR[t] || "#888"}"></i>${esc(TYPE_LABEL[t] || t)}</span>`).join("")
      + `<span><i class="wait"></i>waiting</span>` + (P().ghosts.length ? `<span><i class="ghost"></i>blocked at deadlock</span>` : "")
      + (P().cycle.size ? `<span><i class="ghost cycle"></i>deadlock wait cycle</span>` : "");
    $("#legend-floor").innerHTML = `<span><i style="background:${wfColor(0)}"></i>owned plate (workflow color)</span><span><i class="borrowed"></i>borrowed / shared</span><span><i style="border:1.5px solid var(--busy)"></i>busy</span>`;
  }

  // gantt interaction: click / drag to seek, click a bar to select
  function trackFrac(gantt, clientX) {
    const ov = $(".g-overlay", gantt);
    const r = ov.getBoundingClientRect();
    return Math.min(1, Math.max(0, (clientX - r.left) / r.width));
  }
  ["#gantt-inst", "#gantt-wf"].forEach(sel => {
    const g = $(sel);
    let drag = null;
    g.addEventListener("pointerdown", e => {
      if (!e.target.closest(".g-track") || e.button !== 0) return;
      drag = { x: e.clientX, bar: e.target.closest(".bar[data-b]") };
      g.setPointerCapture(e.pointerId);
      setTime(trackFrac(g, e.clientX) * P().T);
      e.preventDefault();
    });
    g.addEventListener("pointermove", e => {
      if (drag) { setTime(trackFrac(g, e.clientX) * P().T); hideTip(); }
      else showTip(e);
    });
    g.addEventListener("pointerup", e => {
      if (drag && drag.bar && Math.abs(e.clientX - drag.x) < 4) select(drag.bar.dataset.b, true, true);
      drag = null;
    });
    g.addEventListener("pointerleave", hideTip);
  });

  // ---------------------------------------------------------------- tooltip
  const tip = $("#tip");
  function showTip(e) {
    const bar = e.target.closest && e.target.closest(".bar[data-b]");
    if (!bar) return hideTip();
    const b = P().byId[bar.dataset.b];
    if (!b) return hideTip();
    const head = b.ghost
      ? `waiting since t=${b.start}${b.cycle ? " · in the wait cycle" : ""} · ${esc(b.reason)}`
      : `${esc(b.id)} · ${esc(b.kind)}${b.inserted ? " · planner-inserted" : ""} · t ${at(b.start)}–${at(b.end)} (${dur(b.end - b.start)})`;
    tip.innerHTML = `<div class="tt">${esc(b.workflow)} · ${esc(b.label)}</div><div class="tm">${head}</div>
      <div class="tm">locks: ${esc([...b.instruments, ...b.plates].join(", "))}</div>
      <ol class="instr">${(b.instructions || []).map(fmtInstr).join("")}</ol>`;
    tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let x = e.clientX + 14, y = e.clientY + 14;
    if (x + w > innerWidth - 8) x = Math.max(8, e.clientX - w - 14);
    if (y + h > innerHeight - 8) y = Math.max(8, e.clientY - h - 14);
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  function hideTip() { tip.hidden = true; }

  // ---------------------------------------------------------------- floor plan
  const nestEl = {};
  const instEl = {};
  const capEl = {};
  const chips = {};
  let elsewhere = null;

  function buildFloor() {
    const groups = {};
    instSorted.forEach(inst => { (groups[inst.type] = groups[inst.type] || []).push(inst); });
    const html = Object.entries(groups).map(([type, insts]) => `<div class="fgroup"><h3>${esc(TYPE_LABEL[type] || type)}</h3><div class="members">${
      insts.map(inst => {
        const wide = type === "storage" || type === "hotel";
        const cap = inst.nests.reduce((a, n) => a + n.capacity, 0);
        const slots = inst.nests.map(n => {
          const suffix = n.id.includes(".") ? n.id.slice(n.id.indexOf(".") + 1) : "";
          return `<div class="slot" data-nest="${esc(n.id)}">${suffix ? `<span class="sl">${esc(suffix)}</span>` : ""}</div>`;
        }).join("");
        return `<div class="inst${wide ? " wide" : ""}" data-inst="${esc(inst.id)}">
          <div class="ih"><span style="color:${instColor(inst.id)}">■</span>${esc(inst.id)}${wide ? `<span class="cap" data-cap="${esc(inst.id)}"></span>` : ""}</div>
          <div class="now"></div>${slots ? `<div class="slots">${slots}</div>` : ""}</div>`;
      }).join("")}</div></div>`).join("");
    $("#floor").innerHTML = html + `<div class="fgroup" data-elsewhere style="visibility:hidden"><h3>elsewhere</h3><div class="members"><div class="inst"><div class="slots"><div class="slot" data-nest=""></div></div></div></div></div>`;
    $$("#floor [data-nest]").forEach(el => { nestEl[el.dataset.nest] = el; });
    $$("#floor [data-inst]").forEach(el => { instEl[el.dataset.inst] = { el, now: $(".now", el) }; });
    $$("#floor [data-cap]").forEach(el => { capEl[el.dataset.cap] = el; });
    elsewhere = nestEl[""];
    delete nestEl[""];
    const after = document.createElement("div");
    after.className = "floor-note";
    after.id = "floor-note";
    $("#floor").after(after);

    const plates = new Set();
    D.runs.forEach(r => Object.keys(r.initial_locations).forEach(p => plates.add(p)));
    D.runs.forEach(r => r.moves.forEach(m => plates.add(m.plate)));
    [...plates].sort().forEach(p => {
      const c = document.createElement("span");
      const owner = plateOwner[p];
      c.className = "plate";
      c.textContent = p;
      if (owner === undefined) {
        c.classList.add("borrowed");
        c.title = `${p} · borrowed by ${(plateBorrowers[p] || []).join(", ") || "—"}`;
      } else {
        c.style.background = wfColor(owner);
        if (wfDarkInk(owner)) c.classList.add("dark-ink");
        const b = plateBorrowers[p];
        c.title = `${p} · owned by ${D.workflows[owner].id}${b ? " · borrowed by " + b.join(", ") : ""}`;
      }
      chips[p] = c;
    });
  }

  function plateLoc(run, p, plate, t) {
    const ms = p.plateMoves[plate];
    if (ms) {
      const k = lastAtOrBefore(ms, "time", t);
      if (k >= 0) return ms[k].dst;
    }
    return run.initial_locations[plate];
  }

  function updateFloor(t, animate) {
    const run = R(), p = P();
    const moved = [];
    for (const plate in chips) {
      const c = chips[plate];
      const loc = plateLoc(run, p, plate, t);
      const key = loc === undefined ? "∅" : loc;
      if (c.dataset.loc === key) continue;
      moved.push([c, loc, animate ? c.getBoundingClientRect() : null]);
    }
    moved.forEach(([c, loc]) => {
      if (loc === undefined) { c.remove(); c.dataset.loc = "∅"; return; }
      (nestEl[loc] || elsewhere).appendChild(c);
      c.dataset.loc = loc;
    });
    if (animate && moved.length && moved.length <= 40) {
      moved.forEach(([c, loc, first]) => {
        if (!first || loc === undefined || !first.width) return;
        const last = c.getBoundingClientRect();
        const dx = first.left - last.left, dy = first.top - last.top;
        if (!dx && !dy) return;
        c.style.transition = "none";
        c.style.transform = `translate(${dx}px, ${dy}px)`;
        c.getBoundingClientRect();
        c.style.transition = "transform .45s cubic-bezier(.3,.7,.3,1)";
        c.style.transform = "";
      });
    }
    if (moved.length) {
      elsewhere.closest("[data-elsewhere]").style.visibility = elsewhere.children.length ? "" : "hidden";
      for (const id in capEl) {
        const inst = instById[id];
        const n = inst.nests.reduce((a, ns) => a + (nestEl[ns.id] ? nestEl[ns.id].querySelectorAll(".plate").length : 0), 0);
        capEl[id].textContent = `${n} / ${inst.nests.reduce((a, ns) => a + ns.capacity, 0)}`;
      }
    }
    for (const id in instEl) {
      const arr = p.instBlocks[id] || [];
      const k = lastAtOrBefore(arr, "start", t);
      const b = k >= 0 && arr[k].end > t ? arr[k] : null;
      const ie = instEl[id];
      const isBusy = !!b;
      if (ie.el.classList.contains("busy") !== isBusy) ie.el.classList.toggle("busy", isBusy);
      const txt = b ? `${b.workflow} · ${b.label}` : "idle";
      if (ie.now.textContent !== txt) { ie.now.textContent = txt; ie.now.title = txt; }
    }
    const note = $("#floor-note");
    let msg = "";
    if (run.deadlock && t >= run.deadlock.time) msg = `Deadlocked at t=${run.deadlock.time}: ${run.deadlock.waits.length} blocks waiting, nothing can move.`;
    if (run.alarm && t >= run.alarm.time) msg = `Alarm at t=${run.alarm.time}: ${run.alarm.message}`;
    if (note.textContent !== msg) note.textContent = msg;
  }

  // ---------------------------------------------------------------- time / transport
  const scrub = $("#scrub");
  const fill = $(".scrub-fill", scrub), knob = $(".scrub-knob", scrub);

  function setTime(t, animate) {
    const T = P().T;
    S.t = Math.min(T, Math.max(0, t));
    const x = pct(S.t, T);
    S.cursors.forEach(c => { c.style.left = x; });
    fill.style.width = x; knob.style.left = x;
    scrub.setAttribute("aria-valuenow", Math.floor(S.t));
    $("#tnow").textContent = LIVE ? `t = ${hms(S.t)} / ${hms(T)}` : `t = ${Math.floor(S.t)} / ${T} min`;
    updateFloor(S.t, animate);
    lab3dDo(v => v.update(S.t, S.speed));
    if (S.playing && S.zoom > 1) follow();
  }
  function follow() {
    $$(".gantt .g-scroll").forEach(sc => {
      const inner = sc.firstElementChild;
      const lw = parseFloat(getComputedStyle(inner).getPropertyValue("--label-w")) || 132;
      const x = lw + (inner.offsetWidth - lw - 10) * S.t / P().T;
      if (x < sc.scrollLeft + lw || x > sc.scrollLeft + sc.clientWidth - 40) sc.scrollLeft = x - lw - 40;
    });
  }

  function renderScrubMarkers() {
    const T = P().T;
    $(".scrub-markers", scrub).innerHTML = P().markers.map(m =>
      `<div class="mk ${m.cls || ""}" style="left:${pct(m.time, T)}" title="${esc(m.label)}"></div>`).join("");
    scrub.setAttribute("aria-valuemax", T);
  }
  let scrubDrag = false;
  const scrubTo = e => { const r = scrub.getBoundingClientRect(); setTime((e.clientX - r.left) / r.width * P().T); };
  scrub.addEventListener("pointerdown", e => { scrubDrag = true; scrub.setPointerCapture(e.pointerId); scrubTo(e); });
  scrub.addEventListener("pointermove", e => { if (scrubDrag) scrubTo(e); });
  scrub.addEventListener("pointerup", () => { scrubDrag = false; });
  scrub.addEventListener("keydown", e => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      setTime(Math.round(S.t) + (e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 10 : 1), true);
      e.preventDefault(); e.stopPropagation();
    }
  });

  const speedSel = $("#speed");
  speedSel.innerHTML = SPEEDS.map(s => `<option value="${s}">${s} ${LIVE ? "s" : "min"}/s</option>`).join("");
  speedSel.addEventListener("change", () => { S.speed = +speedSel.value; S.speedTouched = true; });
  const DEFAULT_SPEED = LIVE ? 60 : 2;
  function autoSpeed() {
    if (S.speedTouched) return;
    S.speed = DEFAULT_SPEED;
    speedSel.value = String(S.speed);
  }

  const playBtn = $("#play");
  let last = null;
  function tick(ts) {
    if (!S.playing) return;
    if (last !== null) {
      const T = P().T;
      const nt = S.t + (ts - last) / 1000 * S.speed;
      setTime(nt, true);
      if (nt >= T) { setPlaying(false); return; }
    }
    last = ts;
    requestAnimationFrame(tick);
  }
  function setPlaying(on) {
    S.playing = on;
    playBtn.classList.toggle("playing", on);
    playBtn.setAttribute("aria-label", on ? "Pause" : "Play");
    if (on) {
      if (S.t >= P().T) setTime(0);
      last = null;
      requestAnimationFrame(tick);
    }
  }
  playBtn.addEventListener("click", () => setPlaying(!S.playing));

  document.addEventListener("keydown", e => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "select" || tag === "textarea") return;
    if (e.key === " " && tag !== "button") { setPlaying(!S.playing); e.preventDefault(); }
    else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      setTime(Math.round(S.t) + (e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 10 : 1), true);
      e.preventDefault();
    } else if (e.key === "Escape") select(null);
  });

  $$(".zoom [data-zoom]").forEach(b => b.addEventListener("click", () => {
    S.zoom = +b.dataset.zoom;
    $$(".zoom [data-zoom]").forEach(x => x.classList.toggle("on", x === b));
    renderGantts();
    setTime(S.t);
  }));
  $('.zoom [data-zoom="1"]').classList.add("on");

  // ---------------------------------------------------------------- source
  function showSource(key) {
    $$("#src-tabs .subtab").forEach(b => b.classList.toggle("on", b.dataset.src === key));
    $("#src").innerHTML = D.sources[key] || `<p class="muted">(no ${esc(key)} source provided)</p>`;
  }
  $("#src-tabs").addEventListener("click", e => {
    const b = e.target.closest("[data-src]");
    if (b) showSource(b.dataset.src);
  });

  // ---------------------------------------------------------------- compiled
  const rowEl = {};
  function renderCompiled() {
    const wfMeta = Object.fromEntries(D.workflows.map(w => [w.id, w]));
    const chip = (p) => {
      const o = plateOwner[p];
      return o === undefined
        ? `<span class="plate borrowed">${esc(p)}</span>`
        : `<span class="plate${wfDarkInk(o) ? " dark-ink" : ""}" style="background:${wfColor(o)}">${esc(p)}</span>`;
    };
    const short = id => id.slice(id.lastIndexOf("/") + 1);
    $("#compiled").innerHTML = D.compiled.map((c, ci) => {
      const w = wfMeta[c.workflow] || { id: c.workflow, name: "", args: {}, owned: [], borrowed: [] };
      const i = wfIndex[c.workflow] || 0;
      const call = `${esc(w.name)}(${Object.entries(w.args).map(([k, v]) => `${esc(k)}=${esc(JSON.stringify(v))}`).join(", ")})`;
      const vars = Object.entries(c.variables).map(([v, cands]) =>
        `<span><b>${esc(v)}</b> ∈ {${cands.map(esc).join(", ")}} ${D.runs.map((r, ri) => {
          const val = (r.bindings[c.workflow] || {})[v];
          return `<span class="bind" data-bind-run="${ri}" title="${esc(r.mode)}">${esc(r.mode)} → ${esc(val || "—")}</span>`;
        }).join(" ")}</span>`).join("");
      const rows = c.blocks.map(b => {
        const res = b.instruments.map(x => `<span class="rchip${x.startsWith("?") ? " var" : ""}" style="border-color:${x.startsWith("?") ? "var(--muted)" : instColor(x)}">${esc(x)}</span>`).join("")
          + b.plates.map(chip).join("");
        return `<tr class="blk" data-blk="${esc(b.id)}"><td class="bid">${esc(short(b.id))}</td><td><span class="kind">${esc(b.kind)}</span></td>
          <td>${esc(b.label)}</td><td class="dur">${LIVE ? hms(b.duration) : `${b.duration}m`}</td>
          <td class="deps">${b.deps.map(d => `<a data-dep="${esc(d)}">${esc(short(d))}</a>`).join(" ") || '<span class="muted">—</span>'}</td>
          <td><div class="res">${res}</div></td><td><ol class="instr">${b.instructions.map(fmtInstr).join("")}</ol></td></tr>`;
      }).join("");
      return `<details class="wf" data-wf="${esc(c.workflow)}"${ci === 0 ? " open" : ""}>
        <summary><span class="wf-id"><span class="sw" style="background:${wfColor(i)}"></span>${esc(c.workflow)}</span>
          <span class="wf-call">${call}</span>
          <span class="pchips">${w.owned.map(chip).join("")}${w.borrowed.map(chip).join("")}</span>
          <span class="muted small">${c.blocks.length} blocks</span></summary>
        ${vars ? `<div class="vars">${vars}</div>` : ""}
        <table class="blocks"><thead><tr><th>id</th><th>kind</th><th>label</th><th>dur</th><th>deps</th><th>resources</th><th>instructions</th></tr></thead>
        <tbody>${rows}</tbody></table></details>`;
    }).join("");
    $$("#compiled tr.blk").forEach(tr => { rowEl[tr.dataset.blk] = tr; });
    $("#compiled").addEventListener("click", e => {
      const dep = e.target.closest("[data-dep]");
      if (dep) { select(dep.dataset.dep, true, false); e.stopPropagation(); return; }
      const tr = e.target.closest("tr.blk");
      if (tr) select(tr.dataset.blk, false, false);
    });
  }
  function markCompiledForRun() {
    const run = R();
    const waiting = new Set(run.deadlock ? run.deadlock.waits.map(w => baseId(w.block)) : []);
    const cycle = new Set([...P().cycle].map(baseId));
    const ran = new Set(run.blocks.map(b => baseId(b.id)));
    for (const id in rowEl) {
      rowEl[id].classList.toggle("waiting", waiting.has(id));
      rowEl[id].classList.toggle("cycle", cycle.has(id));
      rowEl[id].classList.toggle("notrun", run.blocks.length > 0 && !ran.has(id) && !waiting.has(id));
    }
    $$("#compiled .bind").forEach(el => { el.style.fontWeight = +el.dataset.bindRun === S.run ? "600" : ""; el.style.opacity = +el.dataset.bindRun === S.run ? "1" : ".6"; });
  }

  // ---------------------------------------------------------------- selection (cross-links)
  function select(id, scrollCompiled, toggle) {
    if (toggle && id === S.sel) id = null;
    S.sel = id;
    applySelection(scrollCompiled);
  }
  function applySelection(scrollCompiled) {
    const id = S.sel;
    $$(".bar.sel").forEach(b => b.classList.remove("sel"));
    $$("#compiled tr.sel").forEach(r => r.classList.remove("sel"));
    $$(".plate.sel").forEach(c => c.classList.remove("sel"));
    $$(".g-inner").forEach(g => g.classList.toggle("has-sel", !!id));
    if (!id) return;
    const bars = $$(`.bar[data-b="${attr(id)}"]`);
    bars.forEach(b => b.classList.add("sel"));
    if (!bars.length) $$(".g-inner").forEach(g => g.classList.remove("has-sel"));
    const b = P().byId[id] || compiledById[baseId(id)];
    if (b) (b.plates || []).forEach(pl => { if (chips[pl]) chips[pl].classList.add("sel"); });
    const tr = rowEl[id] || rowEl[baseId(id)];
    if (tr) {
      tr.classList.add("sel");
      if (scrollCompiled) {
        tr.closest("details").open = true;
        tr.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }
  }

  // ---------------------------------------------------------------- run switching
  function setRun(i) {
    S.run = i;
    renderTabs();
    markStatsActive();
    renderAlerts();
    const run = R();
    const unsolved = run.status === "no_solution" || run.status === "infeasible";
    const none = unsolved || (!run.blocks.length && !run.deadlock && !run.alarm);
    $("#run-views").hidden = none;
    $("#run-empty").hidden = !none;
    if (none) {
      const why = run.status === "infeasible"
        ? "the model was proven infeasible"
        : "no feasible plan was found before the solver stopped";
      $("#run-empty").innerHTML = unsolved
        ? `<b>${run.status === "infeasible" ? "Infeasible" : "No solution"}</b>The ${esc(run.mode)} planner found no schedule: ${why} (solver status ${esc(run.solver_status || "unknown")}). Nothing was executed, so there are no charts for this mode.`
        : `<b>Nothing ran</b>This run executed no blocks.`;
    }
    renderUtil();
    renderLegends();
    renderGantts();
    renderScrubMarkers();
    markCompiledForRun();
    autoSpeed();
    for (const p in chips) delete chips[p].dataset.loc;
    lab3dDo(v => v.setRun(run));
    setTime(Math.min(S.t, P().T));
  }

  // ---------------------------------------------------------------- Lab 3D (lab3d.js), built on first sight
  let lab3d = null;
  // a 3D error drops the card and never stops playback or the other views
  function lab3dDo(f) {
    if (!lab3d) return;
    try { f(lab3d); } catch (err) { lab3d = null; $("#card-lab3d").hidden = true; console.error(err); }
  }
  function watchLab3d() {
    const card = $("#card-lab3d");
    if (!window.Lab3D || !window.THREE || !window.IntersectionObserver) { card.hidden = true; return; }
    const io = new IntersectionObserver(es => {
      if (!es.some(e => e.isIntersecting) || lab3d) return;
      io.disconnect();
      try {
        lab3d = Lab3D.create($("#lab3d"), {
          instruments: D.instruments,
          typeColor: type => TYPE_COLOR[type] || "#888888",
          // wfColor with commas: three r128 parses only the comma form of hsl()
          plateColor: p => (plateOwner[p] === undefined ? null : `hsl(${hue(plateOwner[p]).toFixed(1)}, 62%, 52%)`),
        });
      } catch (err) {                   // no WebGL: drop the card, keep the report
        card.hidden = true;
        return;
      }
      lab3dDo(v => { v.setRun(R()); v.update(S.t, S.speed); });
    });
    io.observe(card);
  }

  // ---------------------------------------------------------------- init
  renderStats();
  buildFloor();
  renderCompiled();
  showSource("workflows");
  // Open on the best run: among runs where every workflow finished, the shortest makespan,
  // then the lowest mean completion time, then the earliest tab.
  function bestRun() {
    let best = 0, key = null;
    D.runs.forEach((r, i) => {
      if (!FINISHED.has(statusOf(r)) || r.stats.completed !== r.stats.total) return;
      const k = [r.stats.makespan, r.stats.mean_completion ?? Infinity];
      if (key === null || k[0] < key[0] || (k[0] === key[0] && k[1] < key[1])) { best = i; key = k; }
    });
    return best;
  }
  setRun(bestRun());
  watchLab3d();
})();
