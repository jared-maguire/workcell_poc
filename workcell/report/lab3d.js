// Lab3D: the "Lab 3D" card. Draws Lab3DModel's layout and state at t with three.js (r128,
// vendored), in the control room look (spec docs/superpowers/specs/2026-11-01-lab3d-view-design.md).
// Everything drawn is a function of t; it renders only when something changed and the card is
// on screen.
(function () {
  "use strict";
  const M = window.Lab3DModel;

  const THEMES = {
    light: { bg: 0xeef3f8, floor: 0xe6edf5, grid: [0xb9c9db, 0xd3deea], body: 0xf8fbfd, edge: 0x2b5d93,
             arm: 0x0a8fd0, glow: 0.15, tint: 0.55, run: 0x00a6d6, idle: 0xc4cfdb, borrowed: 0x8a96a3,
             label: "#2b5d93", hemi: 0.9, sun: 0.35, fog: 1.6 },
    dark:  { bg: 0x070b12, floor: 0x0b1320, grid: [0x14304d, 0x0f2238], body: 0x0e1a2a, edge: 0x2a6fb0,
             arm: 0x47e0ff, glow: 0.5, tint: 0.45, run: 0x47e0ff, idle: 0x26323f, borrowed: 0x6b7785,
             label: "#7fb4e8", hemi: 0.25, sun: 0.3, fog: 1.2 },
  };
  const SIZE = {           // w, h, d
    storage: [1.1, 1.6, 0.9], hotel: [0.6, 1.3, 0.6], barcode_scanner: [0.4, 0.35, 0.4],
    head_stamper: [0.8, 0.9, 0.7], thermocycler: [0.7, 0.55, 0.7], liquid_handler: [1.0, 0.8, 0.8],
    heated_shaker: [0.6, 0.35, 0.6], plate_sealer: [0.6, 0.6, 0.6], plate_peeler: [0.6, 0.6, 0.6],
    plate_reader: [0.7, 0.45, 0.6], vacuum_concentrator: [0.8, 0.7, 0.7], centrifuge: [0.8, 0.6, 0.8],
    handoff: [0.5, 0.15, 0.5],
  };
  const CLOSED = new Set(["thermocycler", "centrifuge", "vacuum_concentrator", "plate_reader",
                          "plate_sealer", "plate_peeler"]);    // a plate inside is out of sight
  const STACKED = new Set(["storage", "hotel"]);
  const MAX_STACK = 6;
  const SHOULDER = 2.0, LIFT = 0.6, TRAIL = 0.35;          // TRAIL: fraction of a move drawn behind the plate
  const PLATE = [0.42, 0.09, 0.3];

  function create(el, opts) {
    const insts = opts.instruments;
    const byId = Object.fromEntries(insts.map(i => [i.id, i]));
    const owner = M.nestOwner(insts);
    const L = M.layout(insts);
    const dark = matchMedia("(prefers-color-scheme: dark)");
    let th = THEMES[dark.matches ? "dark" : "light"];

    const canvas = document.createElement("canvas");
    el.appendChild(canvas);
    const r = new THREE.WebGLRenderer({ canvas, antialias: true });
    r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    const scene = new THREE.Scene();
    const cam = new THREE.PerspectiveCamera(38, 4 / 3, 0.1, 2000);
    const hemi = new THREE.HemisphereLight(0xffffff, 0x445566, th.hemi);
    const sun = new THREE.DirectionalLight(0xffffff, th.sun);
    sun.position.set(-4, 9, 5);
    scene.add(hemi, sun);

    // bounds of the layout -> floor, grid and camera
    const pts = [...Object.values(L.pos), ...Object.values(L.arms).map(a => a.at)];
    const xs = pts.map(p => p[0]), zs = pts.map(p => p[1]);
    const minX = Math.min(...xs) - 2, maxX = Math.max(...xs) + 2, minZ = Math.min(...zs) - 2, maxZ = Math.max(...zs) + 2;
    const cx = (minX + maxX) / 2, cz = (minZ + maxZ) / 2, span = Math.max(maxX - minX, maxZ - minZ, 8);
    const floorMat = new THREE.MeshStandardMaterial({ roughness: 0.95 });
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(span * 4, span * 4), floorMat);
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(cx, 0, cz);
    scene.add(floor);
    let grid = null;

    const edgeMat = new THREE.LineBasicMaterial();
    const runEdgeMat = new THREE.LineBasicMaterial();
    const armMat = new THREE.MeshStandardMaterial({ roughness: 0.6 });
    const boxGeo = new THREE.BoxGeometry(1, 1, 1), boxEdges = new THREE.EdgesGeometry(boxGeo);
    const box = (w, h, d, mat, edges) => {
      const m = new THREE.Mesh(boxGeo, mat);
      m.scale.set(w, h, d);
      if (edges) m.add(new THREE.LineSegments(boxEdges, edges));
      return m;
    };

    // instruments: a body facing its arm, a status strip, a label
    const instObj = {};
    for (const [id, [x, z]] of Object.entries(L.pos)) {
      const type = byId[id].type, [w, h, d] = SIZE[type] || [0.6, 0.5, 0.6];
      const g = new THREE.Group();
      g.position.set(x, 0, z);
      const armId = Object.keys(L.arms).find(a => (byId[a].reach || Object.keys(L.pos)).includes(id));
      if (armId && type !== "handoff") g.rotation.y = Math.atan2(L.arms[armId].at[0] - x, L.arms[armId].at[1] - z);
      const body = box(w, h, d, new THREE.MeshStandardMaterial({ roughness: 0.85 }), edgeMat);
      body.position.y = h / 2;
      const strip = new THREE.Mesh(boxGeo, new THREE.MeshBasicMaterial());
      strip.scale.set(w * 0.7, 0.04, 0.02);
      strip.position.set(0, h - 0.12, d / 2 + 0.012);
      const label = makeLabel(id);
      label.position.set(0, h + (STACKED.has(type) ? MAX_STACK * (PLATE[1] + 0.03) + 0.35 : 0.45), 0);
      g.add(body, strip, label);
      scene.add(g);
      instObj[id] = { g, body, strip, label, type, w, h, d, count: -1 };
    }

    scene.updateMatrixWorld(true);      // restingSpot reads instrument matrices before the first render

    // SCARA arms: column, two links, a vertical quill that grips the plate
    const armObj = {};
    for (const [id, a] of Object.entries(L.arms)) {
      const base = new THREE.Group();
      base.position.set(a.at[0], 0, a.at[1]);
      const col = box(0.45, SHOULDER - 0.05, 0.45, armMat, edgeMat);
      col.position.y = (SHOULDER - 0.05) / 2;
      const sh = new THREE.Group(); sh.position.y = SHOULDER;
      const l1 = box(a.link, 0.14, 0.24, armMat, edgeMat); l1.position.x = a.link / 2;
      const el2 = new THREE.Group(); el2.position.x = a.link;
      const l2 = box(a.link, 0.12, 0.2, armMat, edgeMat); l2.position.x = a.link / 2;
      const quill = box(0.08, 1, 0.08, armMat, edgeMat);
      sh.add(l1, el2); el2.add(l2, quill); base.add(col, sh);
      scene.add(base);
      armObj[id] = { base, sh, el2, quill, link: a.link };
    }

    const plateGeo = new THREE.BoxGeometry(...PLATE), plateEdges = new THREE.EdgesGeometry(plateGeo);
    let plates = {}, trails = {}, tl = null, initial = {}, lastT = 0;

    // ---- theme
    function applyTheme() {
      th = THEMES[dark.matches ? "dark" : "light"];
      scene.background = new THREE.Color(th.bg);
      scene.fog = new THREE.Fog(th.bg, span * th.fog, span * 3.2);
      floorMat.color.setHex(th.floor);
      if (grid) scene.remove(grid);
      const n = Math.ceil(span * 4);
      grid = new THREE.GridHelper(n, n * 2, th.grid[0], th.grid[1]);
      grid.position.set(cx, 0.002, cz);
      scene.add(grid);
      hemi.intensity = th.hemi; sun.intensity = th.sun;
      edgeMat.color.setHex(th.edge); runEdgeMat.color.setHex(th.run);
      armMat.color.setHex(th.arm); armMat.emissive.setHex(th.arm); armMat.emissiveIntensity = th.glow;
      for (const o of Object.values(instObj)) {
        // tinted by instrument type, with the 2D floor plan's type colours
        o.body.material.color.setHex(th.body).lerp(new THREE.Color(opts.typeColor(o.type)), th.tint);
        o.body.material.emissive.setHex(th.run);
        drawLabel(o.label, o.label.userData.text, true);
      }
      for (const p of Object.values(plates)) setPlateColor(p);
      for (const t of Object.values(trails)) t.material.color.copy(t.userData.color());
      pose(lastT);
    }
    dark.addEventListener ? dark.addEventListener("change", applyTheme) : dark.addListener(applyTheme);

    // ---- labels: canvas-texture sprites, faded out with camera distance
    function makeLabel(text) {
      const c = document.createElement("canvas"); c.width = 256; c.height = 64;
      const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false }));
      s.scale.set(1.2, 0.3, 1);
      s.userData = { canvas: c, text: "" };
      drawLabel(s, text);
      return s;
    }
    function drawLabel(s, text, force) {
      if (!force && s.userData.text === text) return;
      s.userData.text = text;
      const c = s.userData.canvas, g = c.getContext("2d");
      g.clearRect(0, 0, c.width, c.height);
      g.font = "600 34px ui-monospace, Menlo, Consolas, monospace";
      g.textAlign = "center"; g.textBaseline = "middle";
      g.fillStyle = th.label;
      g.fillText(text, c.width / 2, c.height / 2);
      s.material.map.needsUpdate = true;
    }

    // ---- plates
    function setPlateColor(p) {
      const css = opts.plateColor(p.userData.id);
      p.material.color.set(css || new THREE.Color(th.borrowed));
      p.material.emissive.copy(p.material.color);
    }
    function setRun(run) {
      for (const p of Object.values(plates)) scene.remove(p);
      for (const t of Object.values(trails)) scene.remove(t);
      plates = {}; trails = {};
      tl = M.timeline(run, insts);
      initial = run.initial_locations || {};
      const ids = new Set([...Object.keys(initial), ...tl.moves.map(m => m.plate)]);
      for (const id of [...ids].sort()) {
        const m = new THREE.Mesh(plateGeo, new THREE.MeshStandardMaterial({ roughness: 0.5, emissiveIntensity: 0.35 }));
        m.add(new THREE.LineSegments(plateEdges, edgeMat));
        m.userData.id = id;
        setPlateColor(m);
        scene.add(m);
        plates[id] = m;
        const tr = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ transparent: true, opacity: 0.7 }));
        tr.userData.color = () => m.material.color;
        tr.material.color.copy(m.material.color);
        tr.visible = false;
        scene.add(tr);
        trails[id] = tr;
      }
      pose(lastT);
    }

    // where a plate sits on an instrument: on top, side by side for two nests, stacked in storage
    const tmp = new THREE.Vector3();
    function restingSpot(nest, k) {
      const o = instObj[owner[nest]];
      if (!o) return null;
      const local = new THREE.Vector3(0, o.h + PLATE[1] / 2 + 0.02, 0);
      if (STACKED.has(o.type)) local.y += k * (PLATE[1] + 0.03);
      else if (nest.endsWith(".src")) local.x = -o.w / 4;
      else if (nest.endsWith(".dst")) local.x = o.w / 4;
      else local.x = (k % 2 ? 1 : -1) * Math.ceil(k / 2) * 0.12;
      return o.g.localToWorld(local);
    }
    // a point along an arm's move, swinging around the arm's base, lifted in the middle
    function along(arm, a, b, u) {
      const base = armObj[arm].base.position;
      const angA = Math.atan2(a.z - base.z, a.x - base.x), angB = Math.atan2(b.z - base.z, b.x - base.x);
      let dAng = angB - angA;
      while (dAng > Math.PI) dAng -= 2 * Math.PI;
      while (dAng < -Math.PI) dAng += 2 * Math.PI;
      const e = u < 0.5 ? 2 * u * u : 1 - (-2 * u + 2) ** 2 / 2;
      const rA = Math.hypot(a.x - base.x, a.z - base.z), rB = Math.hypot(b.x - base.x, b.z - base.z);
      const ang = angA + dAng * e, rad = rA + (rB - rA) * e;
      const y = Math.min(SHOULDER - 0.25, a.y + (b.y - a.y) * e + Math.sin(Math.PI * e) * LIFT);
      return new THREE.Vector3(base.x + Math.cos(ang) * rad, y, base.z + Math.sin(ang) * rad);
    }
    function reach(arm, p) {
      const o = armObj[arm], base = o.base.position, Lk = o.link;
      const dx = p.x - base.x, dz = p.z - base.z;
      const d = Math.max(0.15, Math.min(Math.hypot(dx, dz), 2 * Lk - 1e-3));
      const elbow = Math.acos(Math.max(-1, Math.min(1, d * d / (2 * Lk * Lk) - 1)));
      o.sh.rotation.y = -(Math.atan2(dz, dx) - elbow / 2);
      o.el2.rotation.y = -elbow;
      const len = Math.max(0.3, SHOULDER + 0.35 - p.y);       // quill from above the links down to the grip
      o.quill.scale.y = len;
      o.quill.position.set(Lk, p.y + len / 2 - SHOULDER, 0);
    }

    function pose(t) {
      lastT = t;
      if (!tl) return;
      const st = M.stateAt(tl, initial, t);
      const byInst = {};
      for (const [id, s] of Object.entries(st.plates))
        if (s.at !== undefined) (byInst[owner[s.at]] = byInst[owner[s.at]] || []).push([s.at, id]);
      for (const [inst, list] of Object.entries(byInst)) {
        list.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : 1));
        const o = instObj[inst];
        if (!o) { for (const [, id] of list) if (plates[id]) plates[id].visible = false; continue; }
        list.forEach(([nest, id], k) => {
          const p = plates[id], spot = restingSpot(nest, k);
          if (!p) return;
          p.visible = !!spot && !CLOSED.has(o.type) && !(STACKED.has(o.type) && k >= MAX_STACK);
          if (spot) { p.position.copy(spot); p.rotation.y = o.g.rotation.y; }
        });
      }
      for (const [id, o] of Object.entries(instObj)) {
        if (STACKED.has(o.type)) {
          const n = (byInst[id] || []).length;
          if (n !== o.count) { o.count = n; drawLabel(o.label, n ? `${id} ·${n}` : id); }
        }
        const on = st.busy.has(id);
        o.strip.material.color.setHex(on ? th.run : th.idle);
        o.body.material.emissiveIntensity = on ? (th === THEMES.dark ? 0.25 : 0.12) : 0;
        o.body.children[0].material = on ? runEdgeMat : edgeMat;
      }
      // arms: carrying, resting where they last put a plate down, or parked
      for (const arm of Object.keys(armObj)) {
        const c = st.arms[arm];
        if (c) {
          const a = restingSpot(c.src, 0), b = restingSpot(c.dst, 0);
          const p = along(arm, a, b, c.u);
          reach(arm, p);
          const pl = plates[c.plate];
          pl.visible = true; pl.position.copy(p);
          const tr = trails[c.plate], pts = [];
          for (let k = 0; k <= 16; k++) pts.push(along(arm, a, b, Math.max(0, c.u - TRAIL) + (c.u - Math.max(0, c.u - TRAIL)) * k / 16));
          tr.geometry.setFromPoints(pts);
          tr.visible = true;
        } else {
          const nest = st.rest[arm], base = armObj[arm].base.position;
          const spot = nest !== undefined && restingSpot(nest, 0);
          reach(arm, spot ? new THREE.Vector3(spot.x, spot.y + 0.4, spot.z)
                          : new THREE.Vector3(base.x + armObj[arm].link, SHOULDER - 0.6, base.z));
        }
      }
      for (const [id, tr] of Object.entries(trails)) if (tr.visible && !(st.plates[id] && st.plates[id].arm)) tr.visible = false;
      request();
    }

    // ---- camera: orbit by drag, zoom by wheel or pinch; fit the layout on first show
    let yaw = -0.6, pitch = 0.62, dist = span * 1.3;
    const target = new THREE.Vector3(cx, 0.3, cz);
    function aim() {
      cam.position.set(target.x + Math.sin(yaw) * Math.cos(pitch) * dist, Math.sin(pitch) * dist,
                       target.z + Math.cos(yaw) * Math.cos(pitch) * dist);
      cam.lookAt(target);
      const fade = Math.max(0, Math.min(1, (span * 1.6 - dist) / (span * 0.6) + 0.35));
      for (const o of Object.values(instObj)) o.label.material.opacity = fade;
      request();
    }
    const ptrs = new Map();
    let pinch = null;
    canvas.addEventListener("pointerdown", e => { ptrs.set(e.pointerId, [e.clientX, e.clientY]); canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointermove", e => {
      if (!ptrs.has(e.pointerId)) return;
      const [px, py] = ptrs.get(e.pointerId);
      ptrs.set(e.pointerId, [e.clientX, e.clientY]);
      if (ptrs.size === 2) {
        const [a, b] = [...ptrs.values()], dd = Math.hypot(a[0] - b[0], a[1] - b[1]);
        if (pinch) dist = Math.max(3, Math.min(span * 3, dist * pinch / dd));
        pinch = dd;
      } else {
        yaw -= (e.clientX - px) * 0.008;
        pitch = Math.max(0.15, Math.min(1.45, pitch + (e.clientY - py) * 0.008));
      }
      aim();
    });
    const up = e => { ptrs.delete(e.pointerId); pinch = null; };
    canvas.addEventListener("pointerup", up);
    canvas.addEventListener("pointercancel", up);
    canvas.addEventListener("wheel", e => {
      dist = Math.max(3, Math.min(span * 3, dist * Math.exp(e.deltaY * 0.001)));
      aim(); e.preventDefault();
    }, { passive: false });

    // ---- render on demand, only while visible
    let visible = false, queued = false;
    function request() {
      if (queued || !visible) return;
      queued = true;
      requestAnimationFrame(() => { queued = false; r.render(scene, cam); });
    }
    new IntersectionObserver(es => { visible = es[es.length - 1].isIntersecting; request(); }).observe(el);
    function size() {
      const w = el.clientWidth, h = el.clientHeight;
      if (!w || !h) return;
      r.setSize(w, h, false);
      cam.aspect = w / h; cam.updateProjectionMatrix();
      request();
    }
    new ResizeObserver(size).observe(el);

    applyTheme();
    aim();
    size();
    return { setRun, update: pose };
  }

  window.Lab3D = { create };
})();
