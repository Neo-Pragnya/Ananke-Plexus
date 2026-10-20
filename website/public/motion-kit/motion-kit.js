/*!
 * motion-kit.js — playful, accessible micro-interactions for static sites (no dependencies).
 * Part of the "interactive-web-motion" skill · MIT · Neo Pragnya
 *
 * Everything is opt-in through data attributes, and auto-initialised on DOMContentLoaded:
 *
 *   <svg data-morph="blob>star" ...>          shape morphs on hover / focus (also: "circle star hex" cycles on click)
 *   <canvas data-field>                       ambient field of pastel shapes that flee and morph near the cursor
 *   <h1 data-scramble>                        text scrambles then resolves on hover / when revealed
 *   <div data-reveal style="--d:.1s">         fades + un-blurs + rises into view on scroll
 *   <span data-countup>968</span>             counts up from 0 when visible
 *   <a data-magnetic>                         drifts toward the pointer
 *   <div data-tilt>                           3D tilt + a spotlight that follows the pointer (--mx/--my)
 *   <div data-marquee>…</div>                 infinite horizontal scroll of its children (CSS)
 *   class="ants"                              marching-ants dashed border on hover (CSS)
 *
 * Shapes: circle, square, squircle, triangle, diamond, hex, octagon, star, star4, star6, spark, flower, flower4,
 *         flower8, blob, blob2, blob3, cloud, gear. Add your own with MotionKit.registerShape(name, fn(theta) → radius).
 *
 * Respects prefers-reduced-motion (everything resolves instantly, the field draws one still frame).
 * For screenshots/tests: add ?motion=static to the URL, or call MotionKit.settle().
 */
(() => {
  "use strict";

  const N = 120; // samples per shape; every shape shares the same angles, so any shape morphs into any other
  const TAU = Math.PI * 2;
  const ANGLES = Array.from({ length: N }, (_, i) => (i / N) * TAU - Math.PI / 2);
  const params = new URLSearchParams(location.search);
  const reduced =
    params.get("motion") === "static" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const ease = (t) => 1 - Math.pow(1 - t, 4); // easeOutQuart ≈ cubic-bezier(.22,1,.36,1)

  // ------------------------------------------------------------------ shapes as radius(theta) in [0, 1]
  function polygonRadius(vertices) {
    // vertices: [[angle, radius], …] sorted by angle; returns r(theta) along the straight edges
    const pts = vertices.map(([a, r]) => [Math.cos(a) * r, Math.sin(a) * r]);
    return (theta) => {
      const dx = Math.cos(theta);
      const dy = Math.sin(theta);
      let best = 1;
      for (let i = 0; i < pts.length; i++) {
        const [x1, y1] = pts[i];
        const [x2, y2] = pts[(i + 1) % pts.length];
        const ex = x2 - x1;
        const ey = y2 - y1;
        const den = dx * ey - dy * ex;
        if (Math.abs(den) < 1e-9) continue;
        const t = (x1 * ey - y1 * ex) / den; // distance along the ray
        const u = (x1 * dy - y1 * dx) / den; // position along the edge
        if (t > 0 && u >= -1e-6 && u <= 1 + 1e-6) best = t;
      }
      return best;
    };
  }
  const regular = (n, rot = -Math.PI / 2) =>
    polygonRadius(Array.from({ length: n }, (_, i) => [rot + (i / n) * TAU, 1]));
  const star = (n, inner, rot = -Math.PI / 2) =>
    polygonRadius(
      Array.from({ length: n * 2 }, (_, i) => [rot + (i / (n * 2)) * TAU, i % 2 ? inner : 1]),
    );
  const seeded = (seed) => () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  function blob(seed) {
    const rnd = seeded(seed);
    const waves = [2, 3, 5].map((k) => [k, 0.06 + rnd() * 0.07, rnd() * TAU]);
    return (t) => 0.8 + waves.reduce((s, [k, a, p]) => s + a * Math.sin(k * t + p), 0);
  }

  const SHAPES = {
    circle: () => 1,
    square: regular(4, -Math.PI / 4),
    diamond: regular(4),
    triangle: regular(3),
    hex: regular(6),
    octagon: regular(8, -Math.PI / 8),
    squircle: (t) => Math.pow(Math.pow(Math.abs(Math.cos(t)), 4) + Math.pow(Math.abs(Math.sin(t)), 4), -0.25) * 0.78,
    star: star(5, 0.46),
    star4: star(4, 0.38),
    star6: star(6, 0.55),
    spark: star(8, 0.3),
    flower: (t) => 0.74 + 0.26 * Math.cos(5 * (t + Math.PI / 2)),
    flower4: (t) => 0.72 + 0.28 * Math.cos(4 * (t + Math.PI / 2)),
    flower8: (t) => 0.82 + 0.18 * Math.cos(8 * (t + Math.PI / 2)),
    blob: blob(7),
    blob2: blob(29),
    blob3: blob(101),
    cloud: (t) => 0.8 + 0.12 * Math.abs(Math.sin(3.5 * t)),
    gear: (t) => (Math.cos(10 * t) > 0 ? 1 : 0.8),
  };
  const cache = new Map();
  function radii(name) {
    if (!cache.has(name)) {
      const fn = SHAPES[name] || SHAPES.circle;
      const r = ANGLES.map(fn);
      const max = Math.max(...r);
      cache.set(name, r.map((v) => v / max));
    }
    return cache.get(name);
  }

  // Closed Catmull-Rom spline through the sampled points → smooth SVG path
  function toPath(r, cx, cy, R, rot = 0) {
    const p = r.map((v, i) => [cx + Math.cos(ANGLES[i] + rot) * v * R, cy + Math.sin(ANGLES[i] + rot) * v * R]);
    const f = (n) => n.toFixed(2);
    let d = `M${f(p[0][0])},${f(p[0][1])}`;
    for (let i = 0; i < N; i++) {
      const p0 = p[(i - 1 + N) % N];
      const p1 = p[i];
      const p2 = p[(i + 1) % N];
      const p3 = p[(i + 2) % N];
      d += `C${f(p1[0] + (p2[0] - p0[0]) / 6)},${f(p1[1] + (p2[1] - p0[1]) / 6)} ${f(p2[0] - (p3[0] - p1[0]) / 6)},${f(p2[1] - (p3[1] - p1[1]) / 6)} ${f(p2[0])},${f(p2[1])}`;
    }
    return d + "Z";
  }

  // ------------------------------------------------------------------ [data-morph] SVG shapes
  function initMorph(svg) {
    if (svg.__mk) return;
    svg.__mk = true;
    const spec = svg.dataset.morph || "blob>star";
    const hover = spec.includes(">");
    const seq = spec.split(/[>\s]+/).filter(Boolean);
    const vb = (svg.getAttribute("viewBox") || "0 0 100 100").split(/\s+/).map(Number);
    const cx = vb[0] + vb[2] / 2;
    const cy = vb[1] + vb[3] / 2;
    const R = (Math.min(vb[2], vb[3]) / 2) * (parseFloat(svg.dataset.morphScale) || 0.9);
    let path = svg.querySelector("path[data-morph-path]");
    if (!path) {
      path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("data-morph-path", "");
      svg.appendChild(path);
    }
    let current = radii(seq[0]).slice();
    let rot = 0;
    let raf = 0;
    let index = 0;
    path.setAttribute("d", toPath(current, cx, cy, R));

    function go(name, spin = 0.35) {
      cancelAnimationFrame(raf);
      const from = current.slice();
      const to = radii(name);
      const rot0 = rot;
      const rot1 = rot0 + spin;
      if (reduced) {
        current = to.slice();
        path.setAttribute("d", toPath(current, cx, cy, R, rot));
        return;
      }
      const dur = parseFloat(svg.dataset.morphDuration) || 650;
      const start = performance.now();
      const tick = (now) => {
        const t = Math.min(1, (now - start) / dur);
        const e = ease(t);
        current = from.map((v, i) => v + (to[i] - v) * e);
        rot = rot0 + (rot1 - rot0) * e;
        path.setAttribute("d", toPath(current, cx, cy, R, rot));
        if (t < 1) raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    }
    svg.__morphTo = go;

    const host = svg.closest(svg.dataset.morphHost || "a, button, [data-morph-host]") || svg;
    if (hover) {
      host.addEventListener("pointerenter", () => go(seq[1]));
      host.addEventListener("pointerleave", () => go(seq[0], -0.35));
      host.addEventListener("focusin", () => go(seq[1]));
      host.addEventListener("focusout", () => go(seq[0], -0.35));
    } else if (seq.length > 1) {
      const next = () => go(seq[(index = (index + 1) % seq.length)]);
      host.addEventListener("pointerenter", next);
      if (svg.dataset.morphAuto && !reduced) setInterval(next, parseFloat(svg.dataset.morphAuto) || 2600);
    }
  }

  // ------------------------------------------------------------------ [data-field] canvas
  const PALETTE = ["#ff7a59", "#ffd84d", "#b5e655", "#46d1c6", "#a78bfa", "#7cc4ff", "#ff8fb1"];
  const FIELD_SHAPES = ["circle", "star", "hex", "flower", "triangle", "squircle", "blob", "spark", "diamond"];

  function initField(canvas) {
    if (canvas.__mk) return;
    canvas.__mk = true;
    const ctx = canvas.getContext("2d");
    const colors = (canvas.dataset.colors || "").split(",").map((s) => s.trim()).filter(Boolean);
    const palette = colors.length ? colors : PALETTE;
    const count = parseInt(canvas.dataset.field || "", 10) || 34;
    const rnd = seeded(parseInt(canvas.dataset.seed || "", 10) || 42);
    let w = 0;
    let h = 0;
    let dpr = 1;
    const pointer = { x: -9999, y: -9999 };
    const parts = Array.from({ length: count }, (_, i) => {
      const shape = FIELD_SHAPES[i % FIELD_SHAPES.length];
      return {
        x: rnd(),
        y: rnd(),
        size: 10 + rnd() * 26,
        color: palette[i % palette.length],
        shape,
        alt: FIELD_SHAPES[(i + 3) % FIELD_SHAPES.length],
        phase: rnd() * TAU,
        speed: 0.15 + rnd() * 0.35,
        spin: (rnd() - 0.5) * 0.6,
        m: 0, // 0 = own shape, 1 = alt shape
        ox: 0,
        oy: 0,
        filled: rnd() > 0.35,
      };
    });

    function resize() {
      const rect = canvas.getBoundingClientRect();
      dpr = Math.min(2, window.devicePixelRatio || 1);
      w = rect.width;
      h = rect.height;
      canvas.width = Math.max(1, w * dpr);
      canvas.height = Math.max(1, h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    function draw(time) {
      ctx.clearRect(0, 0, w, h);
      const t = time / 1000;
      for (const p of parts) {
        const bx = p.x * w + Math.sin(t * p.speed + p.phase) * 18;
        const by = p.y * h + Math.cos(t * p.speed * 0.8 + p.phase) * 14;
        const dx = bx + p.ox - pointer.x;
        const dy = by + p.oy - pointer.y;
        const dist = Math.hypot(dx, dy);
        const near = Math.max(0, 1 - dist / 160);
        // flee the pointer, then spring back
        p.ox += ((dx / (dist || 1)) * near * 46 - p.ox) * 0.08;
        p.oy += ((dy / (dist || 1)) * near * 46 - p.oy) * 0.08;
        p.m += ((near > 0.05 ? 1 : 0) - p.m) * 0.08;
        const a = radii(p.shape);
        const b = radii(p.alt);
        const r = a.map((v, i) => v + (b[i] - v) * p.m);
        const size = p.size * (1 + near * 0.6);
        ctx.save();
        ctx.translate(bx + p.ox, by + p.oy);
        ctx.rotate(t * p.spin + p.phase);
        ctx.beginPath();
        r.forEach((v, i) => {
          const x = Math.cos(ANGLES[i]) * v * size;
          const y = Math.sin(ANGLES[i]) * v * size;
          if (i) ctx.lineTo(x, y);
          else ctx.moveTo(x, y);
        });
        ctx.closePath();
        ctx.globalAlpha = 0.55 + near * 0.45;
        if (p.filled) {
          ctx.fillStyle = p.color;
          ctx.fill();
        } else {
          ctx.lineWidth = 2.5;
          ctx.strokeStyle = p.color;
          ctx.stroke();
        }
        ctx.restore();
      }
    }
    let raf = 0;
    let visible = true;
    const loop = (time) => {
      if (visible) draw(time);
      raf = requestAnimationFrame(loop);
    };
    resize();
    window.addEventListener("resize", () => {
      resize();
      if (reduced) draw(0);
    });
    const host = canvas.parentElement || canvas;
    host.addEventListener("pointermove", (e) => {
      const rect = canvas.getBoundingClientRect();
      pointer.x = e.clientX - rect.left;
      pointer.y = e.clientY - rect.top;
    });
    host.addEventListener("pointerleave", () => {
      pointer.x = pointer.y = -9999;
    });
    new IntersectionObserver(([entry]) => (visible = entry.isIntersecting)).observe(canvas);
    if (reduced) draw(0);
    else raf = requestAnimationFrame(loop);
    canvas.__stop = () => cancelAnimationFrame(raf);
  }

  // ------------------------------------------------------------------ [data-scramble]
  const GLYPHS = "abcdefghijklmnopqrstuvwxyz<>/*+=~#%&";  // light characters read as "decoding", not as blocks
  function scramble(el) {
    if (reduced || el.__scrambling) return;
    const original = el.dataset.scrambleText || el.textContent || "";
    el.dataset.scrambleText = original;
    el.__scrambling = true;
    const dur = parseFloat(el.dataset.scramble) || 650;
    const start = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - start) / dur);
      const settled = Math.floor(original.length * ease(t));
      el.textContent =
        original.slice(0, settled) +
        original
          .slice(settled)
          .replace(/\S/g, () => GLYPHS[Math.floor(Math.random() * GLYPHS.length)]);
      if (t < 1) requestAnimationFrame(tick);
      else {
        el.textContent = original;
        el.__scrambling = false;
      }
    };
    requestAnimationFrame(tick);
  }
  function initScramble(el) {
    if (el.__mk) return;
    el.__mk = true;
    el.setAttribute("aria-label", el.textContent || "");
    (el.closest("a, button") || el).addEventListener("pointerenter", () => scramble(el));
  }

  // ------------------------------------------------------------------ reveal + countup (IntersectionObserver)
  let onShow = () => {};
  function countUp(el) {
    const target = parseFloat((el.dataset.countupTo || el.textContent || "0").replace(/[^\d.]/g, "")) || 0;
    el.dataset.countupTo = String(target);
    if (reduced) return void (el.textContent = target.toLocaleString());
    const dur = parseFloat(el.dataset.countup) || 1100;
    const start = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - start) / dur);
      el.textContent = Math.round(target * ease(t)).toLocaleString();
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
  const io =
    "IntersectionObserver" in window
      ? new IntersectionObserver(
          (entries) => {
            for (const entry of entries) {
              if (!entry.isIntersecting) continue;
              io.unobserve(entry.target);
              onShow(entry.target);
            }
          },
          { rootMargin: "0px 0px -8% 0px" },
        )
      : null;

  // ------------------------------------------------------------------ pointer effects
  function initMagnetic(el) {
    if (el.__mk || reduced) return;
    el.__mk = true;
    const strength = parseFloat(el.dataset.magnetic) || 0.25;
    el.addEventListener("pointermove", (e) => {
      const r = el.getBoundingClientRect();
      el.style.transform = `translate(${(e.clientX - r.left - r.width / 2) * strength}px, ${(e.clientY - r.top - r.height / 2) * strength}px)`;
    });
    el.addEventListener("pointerleave", () => (el.style.transform = ""));
  }
  function initTilt(el) {
    if (el.__mk) return;
    el.__mk = true;
    const max = parseFloat(el.dataset.tilt) || 6;
    el.addEventListener("pointermove", (e) => {
      const r = el.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width;
      const y = (e.clientY - r.top) / r.height;
      el.style.setProperty("--mx", `${x * 100}%`);
      el.style.setProperty("--my", `${y * 100}%`);
      if (!reduced)
        el.style.transform = `perspective(700px) rotateX(${(0.5 - y) * max}deg) rotateY(${(x - 0.5) * max}deg)`;
    });
    el.addEventListener("pointerleave", () => (el.style.transform = ""));
  }

  const lazy =
    "IntersectionObserver" in window
      ? new IntersectionObserver(
          (entries) => {
            for (const e of entries) {
              if (e.isIntersecting) {
                lazy.unobserve(e.target);
                initMorph(e.target);
              }
            }
          },
          { rootMargin: "300px 0px" },
        )
      : null;

  // ------------------------------------------------------------------ init
  function init(root = document) {
    // Morph glyphs are built lazily: on-screen ones now, the rest as they approach the viewport. Pages with
    // hundreds of glyphs (catalogs) stay fast.
    const vh0 = window.innerHeight || document.documentElement.clientHeight;
    root.querySelectorAll("svg[data-morph]").forEach((svg) => {
      const r = svg.getBoundingClientRect();
      if (!lazy || (r.top < vh0 + 300 && r.bottom > -300 && r.width)) initMorph(svg);
      else lazy.observe(svg);
    });
    root.querySelectorAll("canvas[data-field]").forEach(initField);
    root.querySelectorAll("[data-scramble]").forEach(initScramble);
    root.querySelectorAll("[data-magnetic]").forEach(initMagnetic);
    root.querySelectorAll("[data-tilt]").forEach(initTilt);
    const show = (el) => {
      if (el.classList.contains("is-shown")) return;
      el.classList.add("is-shown");
      if (el.hasAttribute("data-countup")) countUp(el);
      if (el.hasAttribute("data-reveal")) {
        el.querySelectorAll("[data-scramble]").forEach((s) => scramble(s));
        if (el.hasAttribute("data-scramble")) scramble(el);
      }
    };
    onShow = show;
    const vh = window.innerHeight || document.documentElement.clientHeight;
    root.querySelectorAll("[data-reveal], [data-countup]").forEach((el) => {
      if (reduced || !io) return show(el);
      const r = el.getBoundingClientRect();
      // Already on screen at load: reveal on a timer rather than waiting for the observer, so first paint (and
      // headless screenshots) never depend on IntersectionObserver timing.
      if (r.top < vh && r.bottom > 0) setTimeout(() => show(el), 40);
      else io.observe(el);
    });
    document.documentElement.classList.add("mk-ready");
    if (reduced) document.documentElement.classList.add("mk-static");
  }
  function settle() {
    document.querySelectorAll("[data-reveal]").forEach((el) => el.classList.add("is-shown"));
    document.querySelectorAll("[data-countup]").forEach((el) => {
      el.textContent = Number(el.dataset.countupTo || el.textContent).toLocaleString();
    });
  }

  window.MotionKit = {
    init,
    settle,
    shapes: Object.keys(SHAPES),
    registerShape(name, fn) {
      SHAPES[name] = fn;
      cache.delete(name);
    },
    pathFor: (name, cx = 50, cy = 50, R = 45) => toPath(radii(name), cx, cy, R),
    reducedMotion: reduced,
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => init());
  else init();
})();
