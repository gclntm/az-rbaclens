"""Database models, one module per area. Add a table to its area's module, export it
here, then write an Alembic migration (``make check`` compares the two).
"""

from app.db.models.activity import (
    ActivityCompactedDay,
    ActivityEvent,
    ActivityPull,
    DirectoryAuditEvent,
    OperationName,
)
from app.db.models.checks import (
    AppCredential,
    GroupMembership,
    PrincipalOwner,
    PrincipalSignIn,
    RoleAssignment,
    RoleDefinition,
    RoleEligibility,
    Scan,
    ScanGroupResolution,
    ScanOwnerResolution,
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
    "AppCredential",
    "AppSetting",
    "Assessment",
    "AuditEvent",
    "AuthSettings",
    "AzureCredentials",
    "DirectoryAuditEvent",
    "Finding",
    "GroupMembership",
    "JobSchedule",
    "Notification",
    "OperationName",
    "Principal",
    "PrincipalOwner",
    "PrincipalSignIn",
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
    "ScanOwnerResolution",
    "ScanStatus",
    "Scope",
    "ScopeLevel",
    "Todo",
]
