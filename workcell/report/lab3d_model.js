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

  // Where every plate is at time t, which arms are carrying what, and which instruments run.
  function stateAt(tl, initialLocations, t) {
    const plates = {};
    for (const [p, nest] of Object.entries(initialLocations || {})) plates[p] = { at: nest };
    const arms = {};
    for (const m of tl.moves) {
      if (m.t0 > t) break;
      if (m.t1 <= t) plates[m.plate] = { at: m.dst };
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
    return { plates, arms, busy };
  }

  const Lab3DModel = { nestOwner, timeline, stateAt };
  if (typeof module !== "undefined" && module.exports) module.exports = Lab3DModel;
  else root.Lab3DModel = Lab3DModel;
})(typeof globalThis !== "undefined" ? globalThis : this);
