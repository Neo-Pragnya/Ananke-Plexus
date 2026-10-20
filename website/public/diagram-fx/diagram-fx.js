/*!
 * diagram-fx.js — make SVG diagrams feel alive (no dependencies). Part of the "living-diagrams" skill · MIT · Neo Pragnya
 *
 * Works on rendered Mermaid flowcharts/state diagrams and on any SVG whose nodes and edges you can select.
 *
 *   DiagramFX.enhance(svg, {           // all options are optional
 *     nodes: "g.node, g.cluster, [data-node]", // what counts as a node (Mermaid subgraphs are clusters)
 *     edges: "path[data-edge], path.flowchart-link, [data-fx-edge]",
 *     ambient: true,                   // a soft pulse travels a random edge every few seconds
 *   });
 *   DiagramFX.auto();                  // enhance every svg[data-diagram-fx] and figure.mermaid-diagram svg on the page
 *
 * Hover (or focus, or tap) a node: its edges light up with flowing dashes in the direction of travel, pulses run out
 * along outgoing edges and in along incoming ones, its neighbours stay bright and everything else dims. Click pins the
 * focus; click again or press Escape to release. Edges are matched to nodes by geometry (path endpoints against node
 * boxes), so no naming convention is needed.
 *
 * Respects prefers-reduced-motion (highlighting only, no moving pulses) and ?motion=static.
 */
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const reduced =
    new URLSearchParams(location.search).get("motion") === "static" ||
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function graphOf(svg, opts) {
    const nodes = [...svg.querySelectorAll(opts.nodes)].filter((n) => !n.closest("defs"));
    const edges = [...svg.querySelectorAll(opts.edges)].filter((e) => e instanceof SVGGeometryElement);
    // Screen-space boxes, then map each edge's endpoints to the node box they touch (or are nearest to).
    const boxes = nodes.map((n) => n.getBoundingClientRect());
    const ctm = (el) => el.getScreenCTM() || new DOMMatrix();
    const nearest = (pt) => {
      let best = -1;
      let bestD = Infinity;
      boxes.forEach((b, i) => {
        const dx = Math.max(b.left - pt.x, 0, pt.x - b.right);
        const dy = Math.max(b.top - pt.y, 0, pt.y - b.bottom);
        const d = Math.hypot(dx, dy);
        if (d < bestD) {
          bestD = d;
          best = i;
        }
      });
      return bestD < 40 ? best : -1;
    };
    const links = [];
    for (const path of edges) {
      let len = 0;
      try {
        len = path.getTotalLength();
      } catch {
        continue;
      }
      if (!len) continue;
      const m = ctm(path);
      const a = new DOMPoint(path.getPointAtLength(0).x, path.getPointAtLength(0).y).matrixTransform(m);
      const b = new DOMPoint(path.getPointAtLength(len).x, path.getPointAtLength(len).y).matrixTransform(m);
      const from = nearest(a);
      const to = nearest(b);
      if (from < 0 || to < 0 || from === to) continue;
      links.push({ path, from, to, len });
    }
    return { nodes, links };
  }

  function enhance(svg, options = {}) {
    if (!svg || svg.__dfx) return;
    const opts = {
      nodes: "g.node, g.cluster, [data-node]",
      edges: "path[data-edge], path.flowchart-link, [data-fx-edge]",
      ambient: true,
      ...options,
    };
    let graph = graphOf(svg, opts);
    if (graph.links.length === 0) return; // not a graph we understand (e.g. a sequence diagram)
    svg.__dfx = true;
    svg.classList.add("dfx");
    const layer = document.createElementNS(NS, "g");
    layer.setAttribute("class", "dfx-pulses");
    layer.setAttribute("aria-hidden", "true");
    svg.append(layer);

    const mark = () => {
      graph.links.forEach((l) => l.path.classList.add("dfx-edge"));
      graph.nodes.forEach((n, i) => {
        n.classList.add("dfx-node");
        n.dataset.dfx = String(i);
        if (!n.hasAttribute("tabindex")) n.setAttribute("tabindex", "0");
      });
    };
    mark();

    // pulses: small dots that ride an edge from start to end (or end to start)
    const pulses = new Set();
    let raf = 0;
    function pulse(link, reverse = false, cls = "") {
      if (reduced) return;
      const dot = document.createElementNS(NS, "circle");
      dot.setAttribute("r", cls ? "3.2" : "4.2");
      dot.setAttribute("class", `dfx-pulse ${cls}`);
      layer.append(dot);
      pulses.add({ dot, link, reverse, t0: performance.now(), dur: Math.min(1600, 500 + link.len * 2.2) });
      if (!raf) raf = requestAnimationFrame(tick);
    }
    function tick(now) {
      raf = 0;
      for (const p of pulses) {
        const t = Math.min(1, Math.max(0, (now - p.t0) / p.dur));
        const k = p.reverse ? 1 - t : t;
        const pt = p.link.path.getPointAtLength(k * p.link.len);
        // the path may live inside a transformed group: carry its transform over to the pulse layer
        const m = (layer.getScreenCTM() || new DOMMatrix()).inverse().multiply(p.link.path.getScreenCTM() || new DOMMatrix());
        const q = new DOMPoint(pt.x, pt.y).matrixTransform(m);
        p.dot.setAttribute("cx", q.x.toFixed(1));
        p.dot.setAttribute("cy", q.y.toFixed(1));
        p.dot.style.opacity = String(t < 0.12 ? t / 0.12 : t > 0.85 ? (1 - t) / 0.15 : 1);
        if (t >= 1) {
          p.dot.remove();
          pulses.delete(p);
        }
      }
      if (pulses.size) raf = requestAnimationFrame(tick);
    }

    let pinned = -1;
    function focus(i) {
      svg.classList.toggle("dfx-focus", i >= 0);
      const near = new Set([i]);
      graph.links.forEach((l) => {
        const on = l.from === i || l.to === i;
        l.path.classList.toggle("dfx-on", on);
        l.path.classList.toggle("dfx-in", on && l.to === i);
        if (on) near.add(l.from === i ? l.to : l.from);
      });
      graph.nodes.forEach((n, k) => {
        n.classList.toggle("dfx-hot", k === i);
        n.classList.toggle("dfx-near", k !== i && near.has(k));
      });
      if (i >= 0)
        graph.links.forEach((l) => {
          if (l.from === i) pulse(l, false);
          else if (l.to === i) pulse(l, false);
        });
    }
    const indexOf = (target) => {
      const n = target.closest?.(".dfx-node");
      return n && svg.contains(n) ? Number(n.dataset.dfx) : -1;
    };
    svg.addEventListener("pointerover", (e) => {
      const i = indexOf(e.target);
      if (i >= 0 && pinned < 0) focus(i);
    });
    svg.addEventListener("pointerleave", () => pinned < 0 && focus(-1));
    svg.addEventListener("focusin", (e) => {
      const i = indexOf(e.target);
      if (i >= 0) focus(i);
    });
    svg.addEventListener("click", (e) => {
      const i = indexOf(e.target);
      pinned = i === pinned ? -1 : i;
      focus(pinned >= 0 ? pinned : i);
    });
    svg.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        pinned = -1;
        focus(-1);
      }
    });

    // ambient movement: the diagram quietly "breathes" with a pulse along a random edge
    let visible = false;
    new IntersectionObserver(([entry]) => (visible = entry.isIntersecting)).observe(svg);
    if (opts.ambient && !reduced) {
      setInterval(() => {
        if (!visible || document.hidden || svg.classList.contains("dfx-focus")) return;
        pulse(graph.links[Math.floor(Math.random() * graph.links.length)], false, "dfx-ambient");
      }, 1400);
    }
    window.addEventListener("resize", () => {
      graph = graphOf(svg, opts);
      mark();
    });
  }

  function auto(root = document) {
    root.querySelectorAll("svg[data-diagram-fx], figure.mermaid-diagram > svg").forEach((svg) => enhance(svg));
  }

  window.DiagramFX = { enhance, auto, reducedMotion: reduced };
  // Mermaid diagrams render after load: Mermaid.astro dispatches "diagram:rendered" with the new <svg>.
  document.addEventListener("diagram:rendered", (e) => enhance(e.detail?.svg || e.target.querySelector?.("svg")));
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => auto());
  else auto();
})();
