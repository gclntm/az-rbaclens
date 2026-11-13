"""Single source of truth for the deployed version.

Bumped by ``make release V=X.Y.Z`` (scripts/release.sh) together with
backend/pyproject.toml, frontend/package.json and docs/CHANGELOG.md.
``GET /api/health`` reports it, so operators can see what is running.
"""

__version__ = "0.32.0"
