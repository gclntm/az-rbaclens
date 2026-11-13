from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    stage: Literal["test", "prod"] = "test"
    log_level: str = "INFO"

    arm_client_id: str = ""
    arm_client_secret: str = ""
    arm_tenant_id: str = ""

    # Fernet key (44-char base64) used to encrypt the Azure client secret
    # when stored via the settings page. Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str = ""

    database_url: str = "postgresql+psycopg://rbaclens:rbaclens@db:5432/rbaclens_test"

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    # Host names the app answers to besides localhost, *.local names and IP
    # addresses (space-separated, the same variable the frontend's nginx
    # reads). Requests naming any other host are refused (core/request_guard.py).
    extra_server_names: str = ""

    # Stuck-scan sweeper. A worker crash or container restart can leave a
    # scan stranded in ``pending`` / ``running`` forever — nothing flips
    # it to ``failed`` because that transition is owned by the run_scan
    # process. The sweeper periodically scans for those rows and marks
    # them failed so the UI stops spinning.
    #
    # ``stuck_scan_timeout_seconds``: how long a scan can sit in a
    # non-terminal status (measured from ``started_at`` if set, else
    # ``created_at``) before the sweeper marks it failed. Default 1h —
    # legitimate MG walks can take 30+ min, so keep the threshold above
    # the worst-case real scan.
    # ``stuck_scan_sweep_seconds``: how often the sweeper runs. ``0``
    # disables the loop entirely (useful for tests and one-off CLIs).
    stuck_scan_timeout_seconds: int = 3600
    stuck_scan_sweep_seconds: int = 60

    # Job scheduler (app.core.scheduler). ``scheduler_tick_seconds``: how
    # often due schedules are checked; ``0`` disables the loop (tests,
    # CLIs). ``scheduler_max_concurrent_jobs``: worker threads shared by
    # every scheduled snapshot / activity pull.
    scheduler_tick_seconds: int = 60
    # At start, close scans and pulls the previous process left in flight
    # and run their schedules again. Assumes a single backend process;
    # set to false when several replicas share one database.
    recover_jobs_at_start: bool = True
    # Second pass over the Activity Log (core/coverage.py): once a day, from
    # ``second_pass_hour`` (container local time), each subscription's last
    # ``second_pass_hours`` are read again to catch events Azure delivered
    # late. ``second_pass_hours = 0`` turns it off.
    second_pass_hours: int = 48
    second_pass_hour: int = 3
    scheduler_max_concurrent_jobs: int = 2
    # Default schedules (app.core.default_schedules): a daily check of the
    # whole tenant at ``default_check_hour`` (container local time; set TZ
    # to change the zone) and an hourly Activity Log pull per subscription.
    # Created once; schedules an admin deletes stay deleted.
    default_schedules: bool = True
    default_check_hour: int = 6
    # Notification channel (app.core.notify): empty = not connected; rows wait in
    # the outbox. Reserved values for later: "email", "teams".
    notification_channel: str = ""
    # Email channel: the mailbox RBAC Lens sends from (needs the Graph
    # Mail.Send application permission), where messages for the admins'
    # review go, and the app's address for links in messages.
    notification_sender: str = ""
    notification_admin_email: str = ""
    public_url: str = ""
    # SMTP channel (NOTIFICATION_CHANNEL=smtp): plain SMTP, e.g. the Mailpit
    # container of the test and prod stacks; its web inbox for the Settings page.
    smtp_host: str = ""
    smtp_port: int = 25
    notification_inbox_url: str = ""
    # Older waiting messages are not sent when a channel is connected.
    notification_max_age_days: int = 7

    # Retention (app.core.retention). Runs ``retention_first_run_minutes``
    # after startup, then every ``retention_interval_hours`` (``0``
    # disables the loop; Settings → Database can still apply it). Each
    # policy's ``0`` means "keep everything".
    # - events older than ``event_retention_days`` (coverage window 90 d + slack);
    # - per scope, completed snapshots beyond the newest
    #   ``snapshot_keep_per_scope`` — never one that currently owns a scope;
    # - failed snapshots older than ``failed_snapshot_retention_days``;
    # - audit events older than ``audit_retention_days``.
    retention_interval_hours: int = 24
    retention_first_run_minutes: int = 5
    event_retention_days: int = 120
    snapshot_keep_per_scope: int = 6
    failed_snapshot_retention_days: int = 7
    audit_retention_days: int = 365
    # Changes feed entries (app.core.changes) older than this many days.
    change_retention_days: int = 365
    # Records of finished Activity Log pulls (24 a day per subscription);
    # the events themselves follow ``event_retention_days``.
    pull_record_retention_days: int = 30
    # AI wording (core/ai_wording.py): new texts written per day (UTC) at most;
    # texts already written still show. 0 = no limit.
    ai_daily_limit: int = 200


settings = Settings()
