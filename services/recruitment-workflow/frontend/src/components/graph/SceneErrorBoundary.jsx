import { Component } from "react";

/**
 * Keeps a failure of the optional 3D view (WebGL unavailable, a chunk that failed to
 * load) from blanking the whole graph page: the rest of the page stays usable and the
 * recruiter is pointed back to the 2D views.
 */
export default class SceneErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <p role="alert" style={{ margin: 0, padding: "48px 24px", textAlign: "center", color: "var(--v2-text-muted)" }}>
          The 3D view could not start in this browser. The focus and overview views show the same matches.
        </p>
      );
    }
    return this.props.children;
  }
}
