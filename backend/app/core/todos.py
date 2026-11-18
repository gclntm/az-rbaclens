"""To-dos: the changes to make in Azure once a review is finished.

Finishing a round turns every "remove" and "change" decision on a grant
that is still there into a to-do, with the advice's steps and commands at
that moment. RBAC Lens never changes Azure: someone runs the commands, and
the next daily check closes the to-do when it no longer sees the grant.
For a change, the grant that replaces it is reviewed next month like any
other. A to-do still open 14 days after the round was finished is overdue;
its reviewer gets one reminder a week.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.advice import advise_grant
from app.core.advice import context as advice_context
from app.core.current_view import CurrentView, current_view
from app.core.notify import enqueue
from app.core.timeutil import as_utc
from app.db.models import ReviewItem, ReviewRound, Todo

OVERDUE_AFTER = timedelta(days=14)
ACTIONS = ("remove", "change")


def create_for_round(
    session: Session,
    rnd: ReviewRound,
    items: Iterable[ReviewItem],
    now: datetime | None = None,
    view: CurrentView | None = None,
) -> list[Todo]:
    """One to-do per remove/change decision whose grant is still there. Idempotent. The caller commits."""
    from app.core.reviews import remove_command

    now = now or datetime.now(UTC)
    wanted = [i for i in items if i.decision in ACTIONS and i.gone_at is None]
    if not wanted:
        return []
    have = set(
        session.scalars(select(Todo.item_id).where(Todo.item_id.in_([i.id for i in wanted]))).all()
    )
    view = view or current_view(session)
    ctx = advice_context(session, view)
    out: list[Todo] = []
    for item in wanted:
        if item.id in have:
            continue
        ra = view.by_grant_id.get(item.grant_fingerprint)
        adv = advise_grant(ctx, ra) if ra is not None and item.kind == "grant" else None
        if item.kind != "grant":
            from app.core.review_subjects import commands as subject_commands

            how, commands = subject_commands(item.kind, item.decision, item.detail)
        elif item.decision == "remove":
            how = [
                (
                    "Remove the grant with the command below, or in the portal: "
                    f"{item.scope_name or item.scope} › Access control (IAM) › Role assignments."
                )
            ]
            commands = [remove_command(item)]
        else:
            how = (
                list(adv.how)
                if adv and adv.action in ("pim", "replace", "narrow")
                else [
                    "Change how the access is given, as the reviewer noted, then remove the old grant."
                ]
            )
            commands = (
                list(adv.commands)
                if adv and adv.action in ("pim", "replace", "narrow")
                else [remove_command(item)]
            )
        todo = Todo(
            round_id=rnd.id,
            item_id=item.id,
            grant_fingerprint=item.grant_fingerprint,
            kind=item.kind,
            subject_key=item.subject_key,
            detail=item.detail,
            action=item.decision,
            scope=item.scope,
            scope_name=item.scope_name,
            principal_id=item.principal_id,
            principal_name=item.principal_name,
            principal_type=item.principal_type,
            role_name=item.role_name,
            note=item.note,
            advice_rule=(adv.rule if adv and adv.rule != "R00 fine" else None)
            if item.kind == "grant"
            else item.suggested_rule,
            how=how,
            commands=commands,
            reviewer_id=rnd.reviewer_id,
            reviewer_name=rnd.reviewer_name,
            decided_by_name=item.decided_by_name,
            created_at=now,
            due_at=now + OVERDUE_AFTER,
            status="open",
        )
        session.add(todo)
        out.append(todo)
    return out


def close_done(session: Session, view: CurrentView, now: datetime | None = None) -> int:
    """Mark open to-dos done when the latest checks no longer see their grant, owner
    or secret (an owner or secret no check could read stays open). The caller commits."""
    from app.core.owners import current_credentials, current_owners
    from app.core.review_subjects import still_there

    now = now or datetime.now(UTC)
    todos = session.scalars(select(Todo).where(Todo.status == "open")).all()
    objects = {
        ((t.detail or {}).get("object_id") or "").lower() for t in todos if t.kind != "grant"
    }
    owners = current_owners(session, objects) if objects else {}
    creds = current_credentials(session, objects) if objects else {}
    closed = 0
    for todo in todos:
        if todo.kind == "grant":
            gone = todo.grant_fingerprint not in view.by_grant_id
        else:
            gone = still_there(todo.kind, todo.detail, owners, creds) is False
        if gone:
            todo.status, todo.done_at = "done", now
            closed += 1
    return closed


def remind_overdue(session: Session, now: datetime | None = None) -> int:
    """One reminder a week per reviewer with overdue to-dos. The caller commits."""
    now = now or datetime.now(UTC)
    by_reviewer: dict[str | None, list[Todo]] = defaultdict(list)
    for todo in session.scalars(select(Todo).where(Todo.status == "open")).all():
        if as_utc(todo.due_at) < now:
            by_reviewer[todo.reviewer_id].append(todo)
    year, week, _ = now.isocalendar()
    queued = 0
    for reviewer_id, todos in by_reviewer.items():
        if enqueue(
            session,
            "todo_overdue",
            recipient_id=reviewer_id,
            recipient_name=todos[0].reviewer_name,
            subject_ref="todos",
            dedupe_key=f"todo_overdue:{reviewer_id or 'admins'}:{year}-W{week:02d}",
            payload={
                "count": len(todos),
                "oldest_due": min(as_utc(t.due_at) for t in todos).isoformat(),
                "path": "/todos",
            },
        ):
            queued += 1
    return queued


def dismiss(todo: Todo, reason: str, actor_name: str | None) -> Todo:
    """Close a to-do without the change being made (reason required). The caller commits."""
    reason = reason.strip()
    if not reason:
        raise ValueError("say why the change will not be made")
    if todo.status != "open":
        raise ValueError(f"this to-do is already {todo.status}")
    todo.status, todo.dismissed_reason, todo.dismissed_by_name = "dismissed", reason, actor_name
    return todo


def is_overdue(todo: Todo, now: datetime | None = None) -> bool:
    return todo.status == "open" and as_utc(todo.due_at) < (now or datetime.now(UTC))
