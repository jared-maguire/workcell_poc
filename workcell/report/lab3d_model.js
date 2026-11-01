// Lab3DModel: the 3D view's pure model (no DOM, no THREE), so node can test it.
// timeline() rebuilds every instruction's [t0, t1) from its block's start (a block's
// instructions run back to back, as in sim/executor.py); stateAt() is a pure function of t.
(function (root) {
  "use strict";

  const PASSIVE = ["arm", "storage", "hotel", "handoff"];     // never glow

  function nestOwner(instruments) {
    const owner = {};
    for (const inst of instruments) {
      owner[inst.id] = inst.id;
      for (const n of inst.nests || []) owner[n.id] = inst.id;
    }
    return owner;
  }

  function timeline(run, instruments) {
    const owner = nestOwner(instruments);
    const arms = new Set(instruments.filter(i => i.type === "arm").map(i => i.id));
    const cut = run.alarm ? run.alarm.time : Infinity;
    const instr = [];
    for (const b of run.blocks || []) {
      let t = b.start;
      for (const i of b.instructions || []) {
        const t0 = t;
        t += i.duration;
        if (t0 >= cut) break;
        instr.push({ t0, t1: Math.min(t, cut), block: b.id, instrument: i.instrument, op: i.op, args: i.args || {} });
      }
    }
    instr.sort((a, b) => a.t0 - b.t0 || (a.block < b.block ? -1 : a.block > b.block ? 1 : 0));
    const moves = instr.filter(i => i.op === "move" && arms.has(i.instrument)).map(i => ({
      ...i, arm: i.instrument, plate: i.args.plate, src: i.args.src, dst: i.args.dst,
      srcInst: owner[i.args.src], dstInst: owner[i.args.dst],
    }));
    const T = instr.reduce((m, i) => Math.max(m, i.t1), 0);
    const passive = new Set(instruments.filter(i => PASSIVE.includes(i.type)).map(i => i.id));
    return { instr, moves, T, passive };
  }

  // At time t: where every plate is, what each arm carries (`arms`) or where it last put a
  // plate down (`rest`, nest id), and which instruments are running (`busy`).
  function stateAt(tl, initialLocations, t) {
    const plates = {};
    for (const [p, nest] of Object.entries(initialLocations || {})) plates[p] = { at: nest };
    const arms = {}, rest = {};
    for (const m of tl.moves) {
      if (m.t0 > t) break;
      if (m.t1 <= t) { plates[m.plate] = { at: m.dst }; rest[m.arm] = m.dst; }
      else {
        const carry = { arm: m.arm, src: m.src, dst: m.dst, u: (t - m.t0) / (m.t1 - m.t0) };
        plates[m.plate] = carry;
        arms[m.arm] = { plate: m.plate, src: m.src, dst: m.dst, u: carry.u };
      }
    }
    const busy = new Set();
    for (const i of tl.instr) {
      if (i.t0 > t) break;
      if (i.op !== "move" && i.t1 > i.t0 && t < i.t1 && !tl.passive.has(i.instrument)) busy.add(i.instrument);
    }
    return { plates, arms, rest, busy };
  }

  // Automatic floor layout (the workcell file has no positions). Arms sharing handoffs form a
  // group (a line); in a group the arms stand in a row along x, each handoff at the mean of
  // its arms, and each arm's other instruments on a circle around it, leaving a gap toward
  // every handoff. Groups are stacked along z.
  const GAP = 0.6 * Math.PI;        // the opening toward each handoff
  const SPACING = 0.95;             // min distance between neighbouring instruments on a circle

  function layout(instruments) {
    const others = instruments.filter(i => i.type !== "arm").map(i => i.id);
    const arms = instruments.filter(i => i.type === "arm");
    const reach = Object.fromEntries(arms.map(a => [a.id, a.reach || others]));
    const reachedBy = {};
    for (const a of arms) for (const id of reach[a.id]) (reachedBy[id] = reachedBy[id] || []).push(a.id);
    const isHandoff = id => (reachedBy[id] || []).length > 1;
    // groups: connected arms (through shared instruments), in instrument order
    const groupOf = {}, groups = [];
    for (const a of arms) {
      if (groupOf[a.id] !== undefined) continue;
      const g = { arms: [], ids: new Set() }, queue = [a.id];
      groupOf[a.id] = groups.length;
      while (queue.length) {                       // BFS along handoffs gives the row order
        const cur = queue.shift();
        g.arms.push(cur);
        for (const id of reach[cur]) {
          g.ids.add(id);
          for (const b of reachedBy[id]) if (groupOf[b] === undefined) { groupOf[b] = groups.length; queue.push(b); }
        }
      }
      groups.push(g);
    }
    const lone = others.filter(id => !reachedBy[id]);
    const pos = {}, armOut = {}, groupsOut = [];
    let z0 = 0;
    for (const g of groups) {
      const radius = {}, own = {};
      for (const a of g.arms) {
        own[a] = reach[a].filter(id => !isHandoff(id));
        const k = reach[a].length - own[a].length;
        const avail = 2 * Math.PI - Math.min(k, 3) * GAP;
        radius[a] = Math.max(2.3, SPACING * (own[a].length + 1) / avail);
      }
      // arms in a row; the circles open toward each other, so neighbours can overlap them by
      // the depth of the opening and still keep their instruments 1.2 apart
      const at = {}, open = Math.cos(GAP / 2);
      let x = 0;
      g.arms.forEach((a, i) => { if (i) x += open * (radius[g.arms[i - 1]] + radius[a]) + 1.2; at[a] = [x, 0]; });
      const shift = x / 2;
      for (const a of g.arms) at[a][0] -= shift;
      for (const id of g.ids) if (isHandoff(id)) {
        const ps = reachedBy[id].map(a => at[a]);
        pos[id] = [ps.reduce((s, p) => s + p[0], 0) / ps.length, ps.reduce((s, p) => s + p[1], 0) / ps.length];
      }
      for (const a of g.arms) {
        // free angular intervals: the circle minus a GAP around each handoff direction
        const cuts = reach[a].filter(isHandoff).map(h => Math.atan2(pos[h][1] - at[a][1], pos[h][0] - at[a][0]));
        let free = [[0, 2 * Math.PI]];
        for (const c of cuts) {
          const lo = c - GAP / 2, hi = c + GAP / 2;
          free = free.flatMap(([s, e]) => [-2 * Math.PI, 0, 2 * Math.PI].reduce((acc, off) => {
            return acc.flatMap(([s2, e2]) => {
              const l = lo + off, h = hi + off;
              if (h <= s2 || l >= e2) return [[s2, e2]];
              return [[s2, Math.max(s2, l)], [Math.min(e2, h), e2]].filter(([p, q]) => q - p > 1e-9);
            });
          }, [[s, e]]));
        }
        const total = free.reduce((s, [p, q]) => s + q - p, 0);
        const n = own[a].length;
        let placed = 0;
        free.forEach(([p, q], k) => {
          const cnt = k === free.length - 1 ? n - placed : Math.round(n * (q - p) / total);
          for (let j = 0; j < cnt; j++) {
            const ang = p + (j + 0.5) * (q - p) / cnt, id = own[a][placed + j];
            pos[id] = [at[a][0] + Math.cos(ang) * radius[a], at[a][1] + Math.sin(ang) * radius[a]];
          }
          placed += cnt;
        });
      }
      // stack the group below the previous one
      const ids = [...g.ids];
      const zs = ids.map(id => pos[id][1]).concat(g.arms.map(a => at[a][1]));
      const dz = z0 - Math.min(...zs);
      for (const id of ids) pos[id][1] += dz;
      for (const a of g.arms) at[a][1] += dz;
      z0 = Math.max(...ids.map(id => pos[id][1])) + 2;
      for (const a of g.arms) {
        const far = Math.max(0, ...reach[a].map(id => Math.hypot(pos[id][0] - at[a][0], pos[id][1] - at[a][1])));
        armOut[a] = { at: at[a], link: Math.max(0.55 * radius[a], far / 2 * 1.02) };
      }
      groupsOut.push([...g.arms, ...ids]);
    }
    lone.forEach((id, i) => { pos[id] = [(i - (lone.length - 1) / 2) * 1.2, z0]; });
    if (lone.length) groupsOut.push(lone);
    return { pos, arms: armOut, groups: groupsOut };
  }

  const Lab3DModel = { nestOwner, timeline, stateAt, layout };
  if (typeof module !== "undefined" && module.exports) module.exports = Lab3DModel;
  else root.Lab3DModel = Lab3DModel;
})(typeof globalThis !== "undefined" ? globalThis : this);
