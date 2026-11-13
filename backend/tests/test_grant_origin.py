"""Since when a grant exists and who gave it: ingest helpers and API fields."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.db.models import RoleAssignment
from app.tools.grant_origin import name_creators, origin_fields
from tests.test_registry import SUB_SCOPE, _seed_world

CREATED = datetime(2026, 11, 3, 14, 12, tzinfo=UTC)


def test_origin_fields_from_sdk_object_lowercases_creator():
    ra = SimpleNamespace(created_on=CREATED, created_by="AB12-CD", updated_on=None)
    assert origin_fields(ra) == {"created_on": CREATED, "created_by": "ab12-cd", "updated_on": None}


def test_origin_fields_tolerates_missing_and_string_dates():
    assert origin_fields(SimpleNamespace()) == {
        "created_on": None,
        "created_by": None,
        "updated_on": None,
    }
    got = origin_fields(
        SimpleNamespace(created_on="2023-02-14T15:28:28.392353Z", created_by="", updated_on="bad")
    )
    assert got["created_on"] == datetime(2023, 2, 14, 15, 28, 28, 392353, tzinfo=UTC)
    assert got["created_by"] is None
    assert got["updated_on"] is None


def _row(created_by: str | None) -> RoleAssignment:
    return RoleAssignment(
        scope=SUB_SCOPE,
        scope_level="subscription",
        principal_id="p",
        principal_type="User",
        role_definition_id="r",
        created_by=created_by,
    )


def test_name_creators_uses_snapshot_names_then_graph_for_the_rest():
    known, pipeline, ghost, none = _row("sp-1"), _row("sp-tf"), _row("gone"), _row(None)
    asked: list[set[str]] = []

    def resolver(ids, _cred):
        asked.append(set(ids))
        return {"SP-TF": ("sp-terraform", "ServicePrincipal")}

    named = name_creators(
        [known, pipeline, ghost, none],
        principal_types={"sp-1": "ServicePrincipal"},
        principal_names={"sp-1": "sp-deploy"},
        credential=object(),
        resolver=resolver,
    )
    assert named == 2
    assert asked == [{"sp-tf", "gone"}]  # only creators the snapshot could not name
    assert (known.created_by_name, known.created_by_type) == ("sp-deploy", "ServicePrincipal")
    assert (pipeline.created_by_name, pipeline.created_by_type) == (
        "sp-terraform",
        "ServicePrincipal",
    )
    assert ghost.created_by_name is None


def test_name_creators_skips_graph_when_everyone_is_known():
    def resolver(_ids, _cred):
        raise AssertionError("Graph should not be called")

    row = _row("sp-1")
    assert name_creators([row], {"sp-1": "User"}, {"sp-1": "alice"}, object(), resolver) == 1


def test_capabilities_and_assignments_carry_since_and_given_by(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_world(db)
        for ra in db.query(RoleAssignment).all():
            ra.created_on = CREATED
            ra.created_by = "sp-tf"
            ra.created_by_name = "sp-terraform"
            ra.created_by_type = "ServicePrincipal"
        db.commit()
    app_with_sqlite.post("/api/settings/registry/rebuild")

    caps = app_with_sqlite.get("/api/scopes/capabilities", params={"scope": SUB_SCOPE}).json()
    grant = caps["levels"][0]["identities"][0]["grants"][0]
    assert grant["since"].startswith("2026-11-03T14:12")
    assert (grant["given_by_id"], grant["given_by_name"], grant["given_by_type"]) == (
        "sp-tf",
        "sp-terraform",
        "ServicePrincipal",
    )

    rows = app_with_sqlite.get("/api/scopes/assignments", params={"scope": SUB_SCOPE}).json()[
        "rows"
    ]
    assert rows and all(r["given_by_name"] == "sp-terraform" for r in rows)

    page = app_with_sqlite.get("/api/principals/alice-id").json()
    assert page["assignments"] and page["assignments"][0]["given_by_type"] == "ServicePrincipal"


def test_grants_without_origin_report_nulls(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_world(db)
    app_with_sqlite.post("/api/settings/registry/rebuild")
    rows = app_with_sqlite.get("/api/scopes/assignments", params={"scope": SUB_SCOPE}).json()[
        "rows"
    ]
    assert rows and all(r["since"] is None and r["given_by_name"] is None for r in rows)
