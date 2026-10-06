/**
 * The graph views only draw what these helpers return, so the invariants that matter
 * are tested here: a match lands on the ring its persisted flags say (never on one
 * derived from the score), every item is placed on its own ring radius, and the
 * overview keeps "not retrieved" distinct from a low score.
 */
import { describe, expect, it } from "vitest";

import { buildMatrix, coverageOf, defaultJobId, gateBlockedLeaders, radialLayout, ringFor } from "./graphModel.js";

const edge = (over) => ({
  match_id: "m",
  candidate_id: "c1",
  job_id: "j1",
  final_score: 0.6,
  qualified: false,
  hard_gate_passed: true,
  matched_requirements: [],
  missing_requirements: [],
  ...over,
});

describe("ringFor", () => {
  it("reads the ring from the persisted flags only", () => {
    expect(ringFor(edge({ qualified: true }))).toBe("qualified");
    expect(ringFor(edge({ hard_gate_passed: false }))).toBe("gate");
    expect(ringFor(edge({ hard_gate_passed: true }))).toBe("below");
  });

  it("does not promote a high score that failed the gate", () => {
    expect(ringFor(edge({ final_score: 0.95, hard_gate_passed: false }))).toBe("gate");
  });
});

describe("coverageOf", () => {
  it("counts matched over all required skills", () => {
    const c = coverageOf(edge({ matched_requirements: ["a", "b", "c", "d"], missing_requirements: ["e"] }));
    expect(c).toEqual({ matched: 4, total: 5, ratio: 0.8 });
  });

  it("returns a null ratio when the job lists no required skill", () => {
    expect(coverageOf(edge({})).ratio).toBeNull();
  });
});

describe("radialLayout", () => {
  const radii = { qualified: 100, gate: 200, below: 300 };
  const items = [
    { id: "a", ring: "qualified", score: 0.7 },
    { id: "b", ring: "qualified", score: 0.8 },
    { id: "c", ring: "gate", score: 0.9 },
    { id: "d", ring: "below", score: 0.5 },
  ];

  it("puts every item on its ring radius", () => {
    for (const p of radialLayout(items, { cx: 0, cy: 0, radii })) {
      expect(Math.hypot(p.x, p.y)).toBeCloseTo(radii[p.ring], 6);
    }
  });

  it("orders a ring best score first", () => {
    const q = radialLayout(items, { cx: 0, cy: 0, radii }).filter((p) => p.ring === "qualified");
    expect(q.map((p) => p.id)).toEqual(["b", "a"]);
  });

  it("never stacks two items of the same ring on one point", () => {
    const many = Array.from({ length: 12 }, (_, i) => ({ id: String(i), ring: "gate", score: i / 12 }));
    const placed = radialLayout(many, { cx: 0, cy: 0, radii });
    const keys = new Set(placed.map((p) => `${p.x.toFixed(3)},${p.y.toFixed(3)}`));
    expect(keys.size).toBe(12);
  });

  it("can leave a ring out", () => {
    const placed = radialLayout(items, { cx: 0, cy: 0, radii, include: new Set(["qualified", "gate"]) });
    expect(placed.some((p) => p.ring === "below")).toBe(false);
  });
});

describe("buildMatrix", () => {
  const graph = {
    jobs: [
      { job_id: "j1", title: "Job one" },
      { job_id: "j2", title: "Job two" },
    ],
    edges: [
      edge({ candidate_id: "c1", job_id: "j1", final_score: 0.7, qualified: true }),
      edge({ candidate_id: "c2", job_id: "j1", final_score: 0.8 }),
      edge({ candidate_id: "c2", job_id: "j2", final_score: 0.5 }),
    ],
  };

  it("keeps a missing MatchResult as null, not as a zero score", () => {
    const { rows } = buildMatrix(graph, new Map());
    const c1 = rows.find((r) => r.candidateId === "c1");
    expect(c1.cells[1]).toBeNull();
  });

  it("sorts candidates by their best score", () => {
    const { rows } = buildMatrix(graph, new Map());
    expect(rows.map((r) => r.candidateId)).toEqual(["c2", "c1"]);
  });

  it("picks a job with a qualified candidate as the default focus", () => {
    expect(defaultJobId(graph)).toBe("j1");
  });

  it("lists only gate-blocked matches as worth a look", () => {
    const g = { ...graph, edges: [...graph.edges, edge({ candidate_id: "c3", final_score: 0.9, hard_gate_passed: false })] };
    expect(gateBlockedLeaders(g).map((e) => e.candidate_id)).toEqual(["c3"]);
  });
});
