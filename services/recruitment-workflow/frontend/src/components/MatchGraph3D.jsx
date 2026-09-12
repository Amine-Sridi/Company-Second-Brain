import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import ForceGraph3D from "3d-force-graph";
import { buildGraph3DElements } from "./MatchGraph.jsx";
import GraphDetailPanel from "./GraphDetailPanel.jsx";
import layoutStyles from "./MatchGraph.module.css";
import styles from "./MatchGraph3D.module.css";

/**
 * The immersive 3D presentation mode for GraphPage's `/graph` route -- an
 * explicit, opt-in alternative to the default 2D Cytoscape view (MatchGraph.jsx),
 * built directly on the vanilla `3d-force-graph` package (not react-force-graph:
 * a prior audit found react-force-graph pulls in unnecessary AR/VR dependencies
 * and a git+SSH transitive dependency -- see FRONTEND_HANDOFF.md). This component
 * is reached only via React.lazy() from GraphPage, so three.js/3d-force-graph
 * never enter the app's normal bundle -- WebGL itself is only initialized once
 * this component actually mounts, i.e. once the user picks "3D".
 *
 * Reuses, rather than reimplements:
 * - `buildGraph3DElements` (MatchGraph.jsx) for the exact same node/edge id
 *   scheme and persisted-MatchResult field names the 2D view uses -- no score
 *   or qualification recomputation happens here, same as 2D.
 * - `GraphDetailPanel` for the click/selection detail experience -- this
 *   component builds the same plain `{type, kind, refId, label,
 *   connectedEdges}` / `{type: "edge", ...}` selection shape 2D's
 *   buildNodeSelection/buildEdgeSelection produce, from its own plain
 *   force-graph node/link objects instead of Cytoscape elements.
 * - MatchGraph.module.css's `.layout`/`.panel`/`sizeFull` classes for the
 *   side-by-side canvas+panel chrome, so the two modes share one visual
 *   language instead of a second, drifting layout.
 *
 * `hiddenCategories`/`minScore` are controlled filter props from GraphPage
 * (the same ones 2D's MatchGraph receives) -- filtering here re-derives a
 * smaller `{nodes, links}` object and calls `.graphData()` again rather than
 * toggling a per-element visibility flag; 3d-force-graph merges by node `id`
 * across `.graphData()` calls and preserves each surviving node's current
 * position, so re-filtering never restarts or visibly disturbs the layout
 * (only the node SET stays constant -- only edges are ever filtered, since
 * neither control here can remove a node, only hide some of its connections).
 */
// Shared zoomToFit padding for the initial auto-fit, "Reset camera", and "Fit
// graph" -- see the tuning comment at the initial auto-fit call site for why
// this was brought in from 70 after real-browser screenshots showed the
// wider padding left this small dataset looking dim and distant.
const ZOOM_FIT_PADDING = 26;

const MatchGraph3D = forwardRef(function MatchGraph3D(
  { rawGraph, candidateNames, hiddenCategories, minScore, onExit },
  ref
) {
  const containerRef = useRef(null);
  const graphRef = useRef(null); // the ForceGraph3D() instance, once initialized
  const fullDataRef = useRef({ nodes: [], links: [] }); // unfiltered, for lookups (search/focus)
  const [selection, setSelection] = useState(null);
  const [status, setStatus] = useState("loading"); // "loading" | "ready" | "error"

  const { nodes, links } = useMemo(() => buildGraph3DElements(rawGraph, candidateNames), [rawGraph, candidateNames]);

  const reducedMotion = useMemo(
    () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
    []
  );

  // ---------------------------------------------------------------------
  // One-time scene setup. Re-runs only if the container element itself is
  // recreated (never, in practice -- this component mounts once per view
  // switch) -- everything data/filter-dependent below is applied in its own
  // separate effect instead of re-running this whole init.
  // ---------------------------------------------------------------------
  useEffect(() => {
    if (!containerRef.current) return;
    let disposed = false;

    try {
      const style = getComputedStyle(document.documentElement);
      const token = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
      const bg = token("--v2-bg", "#0a0c14");
      const cyan = token("--v2-cyan", "#3fd8e8");
      const violet = token("--v2-violet", "#a78bfa");
      const success = token("--v2-success", "#4ade80");
      const danger = token("--v2-danger", "#fb7185");
      const textColor = token("--v2-text", "#eef0f7");

      // v1.x's real constructor is `new ForceGraph3D(domElement)`, not the
      // older Kapsule-style `ForceGraph3D()(domElement)` double-call some
      // README snippets/tutorials still show for pre-1.0 versions -- verified
      // directly against the installed package's own README/.d.ts.
      const Graph = new ForceGraph3D(containerRef.current)
        .backgroundColor(bg)
        .showNavInfo(false)
        .nodeLabel((node) => `<div style="font:600 12px sans-serif;color:${textColor};padding:2px 4px;">${escapeHtml(node.label)}</div>`)
        .linkLabel(
          (link) =>
            `<div style="font:600 12px sans-serif;color:${textColor};padding:2px 4px;">${Math.round(link.finalScore * 100)}% match${link.qualified ? "" : " (not qualified)"}</div>`
        )
        .nodeThreeObject((node) => buildNodeObject(node, { cyan, violet }))
        .nodeThreeObjectExtend(false)
        .linkColor((link) => (link.qualified ? withAlpha(success, 0.75) : withAlpha(danger, 0.35)))
        .linkWidth((link) => mapRange(link.finalScore ?? 0, 0, 1, 0.5, 2.6))
        .linkOpacity(0.55)
        .linkDirectionalParticles((link) => (link.qualified ? 2 : 0))
        .linkDirectionalParticleWidth(1.4)
        .linkDirectionalParticleSpeed(0.006)
        .linkDirectionalParticleColor(() => cyan)
        .enableNodeDrag(false)
        .onNodeHover((node) => {
          containerRef.current.style.cursor = node ? "pointer" : "grab";
        })
        .onLinkHover((link) => {
          containerRef.current.style.cursor = link ? "pointer" : "grab";
        })
        .onNodeClick((node) => handleFocusNode(node))
        .onLinkClick((link) => {
          setSelection(buildEdge3DSelection(link));
        })
        .onBackgroundClick(() => {
          clearHighlight();
          setSelection(null);
        });

      // Subtle atmospheric depth ("fog" fading distant nodes toward the
      // background color) -- a plain three.js scene property, no extra
      // rendering framework or postprocessing pass needed for it. Density
      // tuned down from an initial 0.0055 after real-browser screenshots (see
      // FRONTEND_HANDOFF.md's Phase 27 entry) showed the *default* framing
      // this small dataset settles at was already far enough away that
      // 0.0055 washed every node/link out to near-invisible -- 0.0016 keeps a
      // real depth cue on a genuinely distant node without fogging out the
      // whole graph at its normal viewing distance.
      Graph.scene().fog = new THREE.FogExp2(bg, 0.0016);
      Graph.scene().add(new THREE.AmbientLight(0xffffff, 0.85));
      Graph.scene().add(new THREE.PointLight(0xffffff, 0.75));

      // Small, curated dataset (a handful of jobs, a few dozen edges) -- tuned
      // so jobs act as strong visual anchors and candidates cluster around the
      // jobs they match, rather than reading as a generic large-graph physics
      // default (see this component's own [NEXT] tuning task in
      // FRONTEND_HANDOFF.md for further passes once this can be seen live).
      Graph.d3Force("charge").strength((node) => (node.kind === "job" ? -260 : -90));
      Graph.d3Force("link").distance((link) => mapRange(link.finalScore ?? 0, 0, 1, 150, 70));
      Graph.d3Force("center").strength(0.9);
      Graph.cooldownTicks(220);

      Graph.width(containerRef.current.clientWidth);
      Graph.height(containerRef.current.clientHeight);

      graphRef.current = Graph;
      fullDataRef.current = { nodes, links };
      Graph.graphData({ nodes, links: links.map((link) => ({ ...link })) });

      // zoomToFit's padding was 70 originally -- real-browser screenshots
      // showed this framed the whole (small) graph so far back that it read
      // as a dim, indistinct cluster rather than "entire graph clearly
      // visible, jobs as anchors." 26 brings the camera meaningfully closer
      // on this dataset size without clipping any node at the graph's edge.
      // Shared by the initial auto-fit, the header's "Reset view", and the
      // "Fit graph" button so all three land on the same framing.
      Graph.onEngineStop(() => {
        if (!disposed) Graph.zoomToFit(reducedMotion ? 0 : 600, ZOOM_FIT_PADDING);
      });

      const resizeObserver = new ResizeObserver(() => {
        if (!containerRef.current) return;
        Graph.width(containerRef.current.clientWidth);
        Graph.height(containerRef.current.clientHeight);
      });
      resizeObserver.observe(containerRef.current);

      setStatus("ready");

      return () => {
        disposed = true;
        resizeObserver.disconnect();
        Graph._destructor?.();
        graphRef.current = null;
      };
    } catch (err) {
      // 3d-force-graph/three.js need a working WebGL context; a handful of
      // real environments (locked-down browser policy, software-only/headless
      // Chromium, exotic old hardware) don't have one. Fail closed to the
      // message below rather than leaving a blank pane -- there is no
      // meaningful 3D fallback beyond "go back to 2D".
      console.error("3D graph initialization failed:", err);
      setStatus("error");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally
    // one-time setup; data/filter updates are applied by the effects below.
  }, []);

  // Re-applies the currently selected node's data label/name after data
  // changes so a stale closure never captures the elements above.
  function handleFocusNode(node) {
    if (!node) return;
    applyHighlightToIds(neighborhoodIdsOf(node.id, fullDataRef.current.links));
    setSelection(buildNode3DSelection(node, fullDataRef.current.links));
    focusCamera(node);
  }

  function focusCamera(node) {
    const Graph = graphRef.current;
    if (!Graph || node.x == null) return;
    const distance = 110;
    const distRatio = 1 + distance / Math.hypot(node.x || 1e-6, node.y || 1e-6, node.z || 1e-6);
    Graph.cameraPosition(
      { x: node.x * distRatio, y: node.y * distRatio, z: node.z * distRatio },
      node,
      reducedMotion ? 0 : 1000
    );
  }

  function neighborhoodIdsOf(nodeId, currentLinks) {
    const ids = new Set([nodeId]);
    for (const link of currentLinks) {
      const sourceId = typeof link.source === "object" ? link.source.id : link.source;
      const targetId = typeof link.target === "object" ? link.target.id : link.target;
      if (sourceId === nodeId) ids.add(targetId);
      if (targetId === nodeId) ids.add(sourceId);
    }
    return ids;
  }

  const applyHighlightToIds = useCallback((idsOrNull) => {
    const Graph = graphRef.current;
    if (!Graph) return;
    Graph.nodeThreeObject((node) => buildNodeObject(node, themeColorsRef.current, idsOrNull)).linkColor((link) => {
      const inFocus =
        !idsOrNull ||
        (idsOrNull.has(typeof link.source === "object" ? link.source.id : link.source) &&
          idsOrNull.has(typeof link.target === "object" ? link.target.id : link.target));
      const base = link.qualified ? themeColorsRef.current.success : themeColorsRef.current.danger;
      return inFocus ? withAlpha(base, link.qualified ? 0.95 : 0.55) : withAlpha(base, 0.06);
    });
  }, []);

  const clearHighlight = useCallback(() => applyHighlightToIds(null), [applyHighlightToIds]);

  // Cached once at init so the highlight effect above doesn't need the whole
  // scene-setup effect's closures re-created.
  const themeColorsRef = useRef({ cyan: "#3fd8e8", violet: "#a78bfa", success: "#4ade80", danger: "#fb7185" });
  useEffect(() => {
    const style = getComputedStyle(document.documentElement);
    const token = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
    themeColorsRef.current = {
      cyan: token("--v2-cyan", "#3fd8e8"),
      violet: token("--v2-violet", "#a78bfa"),
      success: token("--v2-success", "#4ade80"),
      danger: token("--v2-danger", "#fb7185"),
    };
  }, []);

  // Display-only filtering: re-derive the link set from the persisted
  // qualified/final_score fields and hand a fresh links array to
  // .graphData() -- see this component's own doc comment above for why this
  // is safe against layout disruption. Nodes are never filtered (a node with
  // zero currently-visible edges just becomes an isolated point, same as 2D's
  // `.filteredOut` treatment never removes a node either).
  useEffect(() => {
    const Graph = graphRef.current;
    if (!Graph || status !== "ready") return;
    const minScoreFraction = minScore / 100;
    const visibleLinks = fullDataRef.current.links
      .filter((link) => {
        const hiddenByCategory =
          (link.qualified && hiddenCategories.qualified) || (!link.qualified && hiddenCategories.disqualified);
        const hiddenByScore = link.finalScore < minScoreFraction;
        return !hiddenByCategory && !hiddenByScore;
      })
      .map((link) => ({ ...link }));
    Graph.graphData({ nodes: fullDataRef.current.nodes, links: visibleLinks });
  }, [hiddenCategories, minScore, status]);

  const resetView = useCallback(() => {
    clearHighlight();
    setSelection(null);
    graphRef.current?.zoomToFit(reducedMotion ? 0 : 700, ZOOM_FIT_PADDING);
  }, [clearHighlight, reducedMotion]);

  const fitGraph = useCallback(() => {
    graphRef.current?.zoomToFit(reducedMotion ? 0 : 700, ZOOM_FIT_PADDING);
  }, [reducedMotion]);

  const focusNode = useCallback((nodeId) => {
    const Graph = graphRef.current;
    if (!Graph) return;
    const node = Graph.graphData().nodes.find((n) => n.id === nodeId);
    if (!node) return;
    handleFocusNode(node);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useImperativeHandle(ref, () => ({ resetView, focusNode, fitGraph }), [resetView, focusNode, fitGraph]);

  return (
    <div className={layoutStyles.layout}>
      <div className={`${layoutStyles.card} ${layoutStyles.sizeFull} ${styles.card}`}>
        <div className={styles.toolbar}>
          <button type="button" className={layoutStyles.resetButton} onClick={resetView} disabled={status !== "ready"}>
            Reset camera
          </button>
          <button type="button" className={layoutStyles.resetButton} onClick={fitGraph} disabled={status !== "ready"}>
            Fit graph
          </button>
          <button type="button" className={layoutStyles.resetButton} onClick={onExit}>
            ← Return to 2D
          </button>
        </div>
        <div className={styles.sceneWrapper}>
          <div ref={containerRef} className={styles.scene} />
          {status === "loading" && <p className={styles.overlayMessage}>Preparing 3D scene…</p>}
          {status === "error" && (
            <div className={styles.overlayMessage}>
              <p>3D preview isn't available in this browser.</p>
              <button type="button" className={layoutStyles.resetButton} onClick={onExit}>
                Return to 2D
              </button>
            </div>
          )}
        </div>
      </div>
      <GraphDetailPanel selection={selection} sizeClass={layoutStyles.sizeFull} />
    </div>
  );
});

export default MatchGraph3D;

// ---------------------------------------------------------------------------
// Plain-object selection builders -- produce the exact same shape as
// MatchGraph.jsx's buildNodeSelection/buildEdgeSelection (Cytoscape-based) so
// GraphDetailPanel renders identically regardless of which renderer is active.
// ---------------------------------------------------------------------------

function buildNode3DSelection(node, allLinks) {
  const connectedEdges = allLinks
    .filter((link) => {
      const sourceId = typeof link.source === "object" ? link.source.id : link.source;
      const targetId = typeof link.target === "object" ? link.target.id : link.target;
      return sourceId === node.id || targetId === node.id;
    })
    .map((link) => {
      const otherId =
        (typeof link.source === "object" ? link.source.id : link.source) === node.id
          ? typeof link.target === "object"
            ? link.target
            : null
          : typeof link.source === "object"
            ? link.source
            : null;
      return {
        matchId: link.matchId,
        otherLabel: otherId?.label ?? "",
        finalScore: link.finalScore,
        qualified: link.qualified,
      };
    })
    .sort((a, b) => b.finalScore - a.finalScore);

  return { type: "node", kind: node.kind, refId: node.refId, label: node.label, connectedEdges };
}

function buildEdge3DSelection(link) {
  const sourceNode = typeof link.source === "object" ? link.source : null;
  const targetNode = typeof link.target === "object" ? link.target : null;
  return {
    type: "edge",
    candidateId: link.candidateId,
    candidateLabel: sourceNode?.label ?? "",
    jobId: link.jobId,
    jobLabel: targetNode?.label ?? "",
    finalScore: link.finalScore,
    qualified: link.qualified,
    hardGatePassed: link.hardGatePassed,
    matchedRequirements: link.matchedRequirements,
    missingRequirements: link.missingRequirements,
  };
}

// ---------------------------------------------------------------------------
// Three.js node geometry -- candidates are small luminous spheres, jobs are
// larger, visually distinct icosahedra ("geometric" per this task's spec) so
// the two kinds read as different at a glance even before color/size register.
// A faint larger transparent shell behind each mesh stands in for a real bloom
// pass ("glow ... without adding another rendering framework").
// ---------------------------------------------------------------------------

function buildNodeObject(node, colors, focusIds) {
  const isJob = node.kind === "job";
  const dimmed = focusIds ? !focusIds.has(node.id) : false;
  // Radii raised from an initial 7-12 / 3.2-5.6 -- real-browser screenshots
  // (see FRONTEND_HANDOFF.md's Phase 27 entry) showed candidates in
  // particular read as barely-visible dots at this dataset's normal viewing
  // distance once the fog/zoomToFit tuning above was also corrected.
  const radius = isJob ? mapRange(node.degree ?? 1, 1, 10, 9, 15) : mapRange(node.degree ?? 1, 1, 10, 4.5, 7.5);
  const color = isJob ? colors.cyan : colors.violet;
  const geometry = isJob ? new THREE.IcosahedronGeometry(radius, 1) : new THREE.SphereGeometry(radius, 20, 20);
  const material = new THREE.MeshPhongMaterial({
    color,
    emissive: color,
    emissiveIntensity: dimmed ? 0.15 : 0.7,
    shininess: 70,
    transparent: true,
    opacity: dimmed ? 0.25 : 0.95,
  });
  const mesh = new THREE.Mesh(geometry, material);

  // Additive blending, not the default alpha blend -- a real-browser
  // screenshot (Phase 27) showed the original alpha-blended shell reading as
  // a dull dark ring around each node instead of a glow, since alpha-blending
  // a translucent color over an already-dark background barely lightens it.
  // Additive blending actually brightens the pixels behind it, which is what
  // "luminous"/"glow" needs; kept small and low-opacity so it stays a soft
  // halo rather than the "excessive glow" this task explicitly warns against.
  const glowMaterial = new THREE.MeshBasicMaterial({
    color,
    transparent: true,
    opacity: dimmed ? 0.05 : 0.28,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  const glow = new THREE.Mesh(new THREE.SphereGeometry(radius * 1.6, 12, 12), glowMaterial);
  mesh.add(glow);

  return mesh;
}

function mapRange(value, inMin, inMax, outMin, outMax) {
  const clamped = Math.max(inMin, Math.min(inMax, value));
  return outMin + ((clamped - inMin) / (inMax - inMin)) * (outMax - outMin);
}

function withAlpha(hexColor, alpha) {
  const c = new THREE.Color(hexColor);
  const r = Math.round(c.r * 255);
  const g = Math.round(c.g * 255);
  const b = Math.round(c.b * 255);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}
