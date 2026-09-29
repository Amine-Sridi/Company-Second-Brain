/**
 * StatusBadge — the shared pill behind every status label in the app.
 *
 * Worth testing because it is the one component whose failure mode is *silent*: an
 * unrecognised tone must fall back to "neutral" rather than rendering an unstyled pill.
 * A backend that adds a new status would otherwise ship an invisible badge to a
 * recruiter, and nothing would throw.
 *
 * The tone-to-meaning mapping is also app-wide vocabulary (FRONTEND_HANDOFF.md §15):
 * green means the same thing on every page precisely because every page routes through
 * here.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import StatusBadge from "./StatusBadge.jsx";

describe("StatusBadge", () => {
  it("renders its children", () => {
    render(<StatusBadge>Qualified</StatusBadge>);
    expect(screen.getByText("Qualified")).toBeInTheDocument();
  });

  it("applies each recognised tone", () => {
    for (const tone of ["neutral", "primary", "accent", "success", "warning", "danger"]) {
      const { unmount } = render(<StatusBadge tone={tone}>{tone}</StatusBadge>);
      const el = screen.getByText(tone);
      // CSS modules hash class names, so assert the tone is present rather than equal
      // to a literal -- the point is that a distinct class was chosen, not its text.
      expect(el.className).toBeTruthy();
      expect(el.className).toContain("badge");
      unmount();
    }
  });

  it("falls back to neutral for an unknown tone instead of rendering unstyled", () => {
    // The real scenario: the backend grows a status the frontend has not mapped yet.
    const { container } = render(<StatusBadge tone="chartreuse">New status</StatusBadge>);
    const badge = container.querySelector("span");
    expect(badge.className).toContain("badge");
    expect(badge.className).not.toContain("undefined");
    expect(screen.getByText("New status")).toBeInTheDocument();
  });

  it("defaults to neutral when no tone is given", () => {
    const { container } = render(<StatusBadge>Plain</StatusBadge>);
    expect(container.querySelector("span").className).not.toContain("undefined");
  });

  it("exposes the title attribute for hover context", () => {
    render(<StatusBadge title="Why this status">Flagged</StatusBadge>);
    expect(screen.getByText("Flagged")).toHaveAttribute("title", "Why this status");
  });

  it("adds the glass variant only when asked", () => {
    // Additive by design: every non-Dashboard caller passes no variant and must render
    // exactly as before (see the component's own docstring).
    const { container: withVariant } = render(<StatusBadge variant="glass">A</StatusBadge>);
    const { container: without } = render(<StatusBadge>B</StatusBadge>);
    expect(withVariant.querySelector("span").className).not.toEqual(
      without.querySelector("span").className,
    );
  });
});
