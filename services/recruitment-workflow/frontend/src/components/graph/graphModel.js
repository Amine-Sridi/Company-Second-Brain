/**
 * Pure helpers behind the knowledge graph views. No React, no DOM: every function
 * takes the backend's graph payload (GET /v1/graph/candidates-jobs) and returns
 * plain data, so the rules are unit-tested on their own (graphModel.test.js).
 *
 * Nothing here recomputes a score or a qualification. The ring a match falls on is
 * read straight off two persisted MatchResult flags:
 *   qualified                     -> "qualified"  (inner ring)
 *   not qualified, gate failed    -> "gate"       (middle ring: missing required skills)
 *   not qualified, gate passed    -> "below"      (outer ring: below the score threshold)
 */

export const RINGS = ["qualified", "gate", "below"];

export const RING_LABELS = {
  qualified: "Qualified",
  gate: "Missing required skills",
  below: "Below the score threshold",
};

export function ringFor(edge) {
  if (edge.qualified) return "qualified";
  return edge.hard_gate_passed ? "below" : "gate";
}

/** Required-skill coverage of one match, from its matched/missing lists. */
export function coverageOf(edge) {
  const matched = edge.matched_requirements?.length ?? 0;
  const missing = edge.missing_requirements?.length ?? 0;
  const total = matched + missing;
  return { matched, total, ratio: total ? matched / total : null };
}

export function initialsOf(name) {
  const parts = String(name || "?").trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "?";
  const last = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (first + last).toUpperCase();
}

/**
 * Per-job summary rows: match and qualified counts, best score. Jobs keep the
 * backend's order; `jobsWithQualifiedFirst` is a convenience for picking a default.
 */
export function summarizeJobs(graph) {
  const byJob = new Map(graph.jobs.map((job) => [job.job_id, { ...job, matches: 0, qualified: 0, best: 0 }]));
  for (const edge of graph.edges) {
    const row = byJob.get(edge.job_id);
    if (!row) continue;
    row.matches += 1;
    if (edge.qualified) row.qualified += 1;
    row.best = Math.max(row.best, edge.final_score);
  }
  return [...byJob.values()];
}

export function defaultJobId(graph) {
  const rows = summarizeJobs(graph);
  const withQualified = rows.filter((row) => row.qualified > 0);
  return (withQualified[0] ?? rows[0])?.job_id ?? null;
}

/**
 * Places the matches of one centre (a job, or a candidate) on three concentric rings.
 * Within a ring, items are ordered by score (best first) and spread evenly. The outer
 * rings start half a step (of the inner ring) later, so their items fall between the
 * inner ones instead of right behind them, where the labels would collide.
 *
 * @param {Array<{id: string, ring: string, score: number}>} items
 * @param {{cx: number, cy: number, radii?: object, include?: Set<string>}} options
 * @returns {Array<{id, ring, score, x, y, angle, rank}>}
 */
export function radialLayout(items, { cx, cy, radii = { qualified: 130, gate: 215, below: 295 }, include } = {}) {
  const innerCount = Math.max(items.filter((item) => item.ring === "qualified").length, 1);
  const half = 180 / innerCount;
  const start = { qualified: -90, gate: -90 + half, below: -90 + half / 2 };
  const placed = [];
  for (const ring of RINGS) {
    if (include && !include.has(ring)) continue;
    const list = items.filter((item) => item.ring === ring).sort((a, b) => b.score - a.score);
    const step = 360 / Math.max(list.length, 1);
    list.forEach((item, rank) => {
      const angle = start[ring] + step * rank;
      const rad = (angle * Math.PI) / 180;
      placed.push({
        ...item,
        rank,
        angle,
        x: cx + radii[ring] * Math.cos(rad),
        y: cy + radii[ring] * Math.sin(rad),
      });
    });
  }
  return placed;
}

/** Items of one job, ready for radialLayout. */
export function jobFocusItems(graph, jobId, names) {
  return graph.edges
    .filter((edge) => edge.job_id === jobId)
    .map((edge) => ({
      id: edge.candidate_id,
      label: names?.get(edge.candidate_id) ?? "Unknown candidate",
      ring: ringFor(edge),
      score: edge.final_score,
      edge,
    }));
}

/** Items of one candidate (jobs around them), ready for radialLayout. */
export function candidateFocusItems(graph, candidateId) {
  const titles = new Map(graph.jobs.map((job) => [job.job_id, job.title]));
  return graph.edges
    .filter((edge) => edge.candidate_id === candidateId)
    .map((edge) => ({
      id: edge.job_id,
      label: titles.get(edge.job_id) ?? "Job",
      ring: ringFor(edge),
      score: edge.final_score,
      edge,
    }));
}

/**
 * Candidate x job matrix for the overview. Rows are candidates sorted by their best
 * score (or by name), columns are the given jobs; a cell is null when retrieval did
 * not return that candidate for that job (no MatchResult exists).
 */
export function buildMatrix(graph, names, { jobIds, sortBy = "best" } = {}) {
  const columns = (jobIds ?? graph.jobs.map((job) => job.job_id)).map((id) => graph.jobs.find((job) => job.job_id === id)).filter(Boolean);
  const cells = new Map();
  for (const edge of graph.edges) cells.set(`${edge.candidate_id}|${edge.job_id}`, edge);
  const candidateIds = [...new Set(graph.edges.map((edge) => edge.candidate_id))];
  const rows = candidateIds.map((candidateId) => {
    const row = columns.map((job) => cells.get(`${candidateId}|${job.job_id}`) ?? null);
    const scores = row.filter(Boolean).map((edge) => edge.final_score);
    return {
      candidateId,
      name: names?.get(candidateId) ?? "Unknown candidate",
      cells: row,
      best: scores.length ? Math.max(...scores) : 0,
      qualifiedCount: row.filter((edge) => edge?.qualified).length,
    };
  });
  rows.sort(sortBy === "name" ? (a, b) => a.name.localeCompare(b.name) : (a, b) => b.best - a.best);
  return { columns, rows };
}

/** High scores blocked by the required-skill gate: the "worth a look" list. */
export function gateBlockedLeaders(graph, limit = 6) {
  return graph.edges
    .filter((edge) => !edge.qualified && !edge.hard_gate_passed)
    .sort((a, b) => b.final_score - a.final_score)
    .slice(0, limit);
}

export function percent(score) {
  return Math.round(score * 100);
}
