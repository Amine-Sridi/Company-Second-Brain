"""
Shared pytest setup for the recruitment-workflow test suite.

Inside Docker, /app is both the service root and the parent of /app/shared/ (the
Dockerfile COPYs shared/ there), so `import shared...` resolves without help. In a
local/host test run there is no such combined directory, so both the service root
(for `main`, `db`, `resume_intelligence_client`, etc.) and the monorepo root (for
`shared`, since main.py imports shared.llm/shared.schemas/shared.observability) need
to be added to sys.path explicitly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_candidates = [_SERVICE_ROOT] + list(_SERVICE_ROOT.parents)
_REPO_ROOT = next((p for p in _candidates if (p / "shared").is_dir()), _SERVICE_ROOT)

for _path in (_SERVICE_ROOT, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# main.py's db.py needs DATABASE_URL to be a syntactically valid URL at import time
# (engine creation is lazy -- no real Postgres is required unless a test actually
# issues a query).
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost:5432/unused")
