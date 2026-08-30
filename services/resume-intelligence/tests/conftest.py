"""
Shared pytest setup for the resume-intelligence test suite.

Inside Docker, /app is both the service root and the parent of /app/shared/ (the
Dockerfile COPYs shared/ there), so `import shared...` resolves without help. In
local/host test runs there is no such combined directory, so both the service root
(for `pipeline`, `db`, `api`, `schemas`, `config`) and the monorepo root (for
`shared`, since pipeline/parsing_agent.py and api.py import shared.llm/shared.schemas)
need to be added to sys.path explicitly.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parents[1]

# In Docker, /app IS the service root and /app/shared/ already sits right under it
# (the Dockerfile COPYs shared/ there), so the service root doubles as the repo
# root. On the host, shared/ is two levels up instead (services/resume-intelligence
# -> services -> repo root). Prefer whichever candidate actually has a shared/ dir.
_candidates = [_SERVICE_ROOT] + list(_SERVICE_ROOT.parents)
_REPO_ROOT = next((p for p in _candidates if (p / "shared").is_dir()), _SERVICE_ROOT)

for _path in (_SERVICE_ROOT, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
