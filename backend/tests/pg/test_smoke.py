"""Every test that boots the app, rerun on Postgres (their ``app_with_sqlite`` is the
Postgres fixture of ``tests/pg/conftest.py``): JSON paths, time aggregates,
cascades and upserts run on the real database, not only on SQLite.

Left out: the two database-stats tests, written for SQLite (Postgres row counts
are estimates until ANALYZE). A new test module that boots the app goes in this list.
"""

from __future__ import annotations

from tests.test_activity import *  # noqa: F403
from tests.test_activity_compaction import *  # noqa: F403
from tests.test_activity_payload import *  # noqa: F403
from tests.test_advice import *  # noqa: F403
from tests.test_ai_wording import *  # noqa: F403
from tests.test_audit import *  # noqa: F403
from tests.test_auth import *  # noqa: F403
from tests.test_auth_settings_protected import *  # noqa: F403
from tests.test_changes import *  # noqa: F403
from tests.test_coverage import *  # noqa: F403
from tests.test_csv_safe import *  # noqa: F403
from tests.test_database import *  # noqa: F403
from tests.test_entra_audit import *  # noqa: F403
from tests.test_findings import *  # noqa: F403
from tests.test_grant_origin import *  # noqa: F403
from tests.test_grants import *  # noqa: F403
from tests.test_iam_access import *  # noqa: F403
from tests.test_job_lifecycle import *  # noqa: F403
from tests.test_jobs import *  # noqa: F403
from tests.test_mgmt_group_log import *  # noqa: F403
from tests.test_notifications import *  # noqa: F403
from tests.test_notify_email import *  # noqa: F403
from tests.test_operation_names import *  # noqa: F403
from tests.test_pim import *  # noqa: F403
from tests.test_pim_views import *  # noqa: F403
from tests.test_principals import *  # noqa: F403
from tests.test_registry import *  # noqa: F403
from tests.test_report import *  # noqa: F403
from tests.test_request_guard import *  # noqa: F403
from tests.test_responsible import *  # noqa: F403
from tests.test_retention import *  # noqa: F403
from tests.test_reviews import *  # noqa: F403
from tests.test_role_coverage import *  # noqa: F403
from tests.test_role_definitions import *  # noqa: F403
from tests.test_rotate_encryption_key import *  # noqa: F403
from tests.test_scan_root import *  # noqa: F403
from tests.test_scope_browser import *  # noqa: F403
from tests.test_scope_capabilities import *  # noqa: F403
from tests.test_scope_views import *  # noqa: F403
from tests.test_settings import *  # noqa: F403
from tests.test_staleness import *  # noqa: F403
from tests.test_sweeper import *  # noqa: F403
from tests.test_todos import *  # noqa: F403
from tests.test_token_check import *  # noqa: F403
from tests.test_visibility import *  # noqa: F403

del test_stats_returns_table_list_on_sqlite, test_stats_row_counts_reflect_data  # noqa: F821
