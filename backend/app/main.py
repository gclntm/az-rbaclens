import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import activity as activity_api
from app.api import advice as advice_api
from app.api import ai as ai_api
from app.api import audit as audit_api
from app.api import auth as auth_api
from app.api import changes as changes_api
from app.api import (
    dashboard,
    database,
    health,
    iam_access,
    principal_views,
    role_definitions,
    scan_export,
    scan_results,
    scope_browser,
    scope_capabilities,
    scope_views,
)
from app.api import data_kept as data_kept_api
from app.api import findings as findings_api
from app.api import grants as grants_api
from app.api import jobs as jobs_api
from app.api import notifications as notifications_api
from app.api import registry as registry_api
from app.api import report as report_api
from app.api import responsible as responsible_api
from app.api import reviews as reviews_api
from app.api import search as search_api
from app.api import settings as settings_api
from app.api import todos as todos_api
from app.core.audit import AuditMiddleware
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.request_guard import RequestGuardMiddleware
from app.core.retention import run_retention_loop
from app.core.scheduler import run_scheduler_loop
from app.core.security import require_admin, require_auth, warn_if_open
from app.core.sweeper import run_sweeper_loop
from app.core.visibility import guard
from app.version import __version__


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    await asyncio.to_thread(warn_if_open)
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(run_sweeper_loop(stop)),
        asyncio.create_task(run_scheduler_loop(stop)),
        asyncio.create_task(run_retention_loop(stop)),
    ]
    try:
        yield
    finally:
        stop.set()
        await asyncio.gather(*tasks)


app = FastAPI(title="RBAC Lens", version=__version__, lifespan=lifespan)

# Added first, so it runs last: the audit log still records a refused request,
# and CORS answers a browser's preflight before it gets here.
app.add_middleware(RequestGuardMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuditMiddleware)

# Public — must stay reachable without a token so the SPA can bootstrap
# MSAL (/auth/config) and orchestrators can liveness-probe (health).
app.include_router(health.router, prefix="/api")
app.include_router(auth_api.router, prefix="/api")

# Authenticated — when AuthSettings.enabled is true, every request to
# these routers must carry a valid Bearer token from the configured
# tenant. When disabled, the dependency is a no-op.
_authed = [Depends(require_auth), Depends(guard)]
# Admin-only — same auth check plus a group-membership gate when
# ``admin_group_ids`` is set. When empty (default), every signed-in
# user counts as admin (single-team backward-compatible behaviour).
_admin = [Depends(require_admin)]

app.include_router(iam_access.router, prefix="/api", dependencies=_authed)
app.include_router(scan_results.router, prefix="/api", dependencies=_authed)
app.include_router(role_definitions.router, prefix="/api", dependencies=_authed)
app.include_router(scan_export.router, prefix="/api", dependencies=_authed)
app.include_router(findings_api.router, prefix="/api", dependencies=_authed)
app.include_router(findings_api.global_router, prefix="/api", dependencies=_authed)
# scope_views before registry: its fixed /scopes/{assignments,findings,history}
# must win over registry's greedy /scopes/{scope_id:path}.
app.include_router(scope_views.router, prefix="/api", dependencies=_authed)
app.include_router(scope_capabilities.router, prefix="/api", dependencies=_authed)
app.include_router(principal_views.router, prefix="/api", dependencies=_authed)
app.include_router(grants_api.router, prefix="/api", dependencies=_authed)
app.include_router(changes_api.router, prefix="/api", dependencies=_authed)
app.include_router(responsible_api.router, prefix="/api", dependencies=_authed)
app.include_router(reviews_api.router, prefix="/api", dependencies=_authed)
app.include_router(todos_api.router, prefix="/api", dependencies=_authed)
app.include_router(report_api.router, prefix="/api", dependencies=_authed)
app.include_router(ai_api.router, prefix="/api", dependencies=_authed)
app.include_router(registry_api.router, prefix="/api", dependencies=_authed)
app.include_router(search_api.router, prefix="/api", dependencies=_authed)
app.include_router(activity_api.router, prefix="/api", dependencies=_authed)
app.include_router(jobs_api.router, prefix="/api", dependencies=_authed)
app.include_router(dashboard.router, prefix="/api", dependencies=_authed)
app.include_router(scope_browser.router, prefix="/api", dependencies=_authed)
# Sign-in settings: admins only once sign-in is on; open while it is off so it
# can be set up. Locked out? ``make auth-off <env>`` from the host.
app.include_router(auth_api.settings_router, prefix="/api", dependencies=_admin)
app.include_router(settings_api.router, prefix="/api", dependencies=_admin)
app.include_router(database.router, prefix="/api", dependencies=_admin)
app.include_router(data_kept_api.router, prefix="/api", dependencies=_admin)
app.include_router(audit_api.router, prefix="/api", dependencies=_admin)
app.include_router(registry_api.admin_router, prefix="/api", dependencies=_admin)
app.include_router(notifications_api.router, prefix="/api", dependencies=_admin)
app.include_router(advice_api.router, prefix="/api", dependencies=_admin)
app.include_router(ai_api.settings_router, prefix="/api", dependencies=_admin)
