"""Database models, one module per area. Add a table to its area's module, export it
here, then write an Alembic migration (``make check`` compares the two).
"""

from app.db.models.activity import (
    ActivityCompactedDay,
    ActivityEvent,
    ActivityPull,
    OperationName,
)
from app.db.models.checks import (
    GroupMembership,
    RoleAssignment,
    RoleDefinition,
    RoleEligibility,
    Scan,
    ScanGroupResolution,
    ScanStatus,
    ScopeLevel,
)
from app.db.models.findings import (
    Finding,
    ReviewDecision,
    ScanCoverage,
)
from app.db.models.registry import (
    AccessChange,
    Principal,
    Scope,
)
from app.db.models.reviews import (
    AiText,
    Assessment,
    Notification,
    Responsibility,
    ResponsibleSuggestion,
    ReviewItem,
    ReviewRound,
    Todo,
)
from app.db.models.settings import (
    AppSetting,
    AuditEvent,
    AuthSettings,
    AzureCredentials,
    JobSchedule,
)

__all__ = [
    "AccessChange",
    "ActivityCompactedDay",
    "ActivityEvent",
    "ActivityPull",
    "AiText",
    "AppSetting",
    "Assessment",
    "AuditEvent",
    "AuthSettings",
    "AzureCredentials",
    "Finding",
    "GroupMembership",
    "JobSchedule",
    "Notification",
    "OperationName",
    "Principal",
    "Responsibility",
    "ResponsibleSuggestion",
    "ReviewDecision",
    "ReviewItem",
    "ReviewRound",
    "RoleAssignment",
    "RoleDefinition",
    "RoleEligibility",
    "Scan",
    "ScanCoverage",
    "ScanGroupResolution",
    "ScanStatus",
    "Scope",
    "ScopeLevel",
    "Todo",
]
