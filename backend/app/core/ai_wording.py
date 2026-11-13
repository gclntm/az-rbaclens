"""Optional AI wording: Claude rewrites an advice in plain words. Off by default.

The playbook (core/advice.py) decides every action and decision; this only
rewrites the explanation for a reader who is not a cloud specialist. It
never changes the advice, the report's statuses or numbers.

What is sent is built here and nowhere else: the role, the place, what the
role allows, how it is given, the advice's title and steps, and day counts.
People's names, UPNs and object ids are never sent (the identity becomes
"a person", "a group", "an app"…). ``grant_facts`` / ``identity_facts`` are
also what Settings and the "What is sent" view show.

Texts are cached by a hash of the model, the prompt version and the facts,
so the same facts give the same text and no repeat call. The API key is
stored encrypted with ``ENCRYPTION_KEY`` (core/crypto.py); without a stored
key, ``ANTHROPIC_API_KEY`` from the environment is used.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.advice import AdviceContext
from app.core.advice_texts import RULES
from app.core.config import settings
from app.core.crypto import decrypt, encrypt, encryption_available
from app.core.timeutil import as_utc
from app.db.models import AiText, AppSetting, RoleAssignment
from app.schemas.advice import Advice, IdentityAdvice

SETTINGS_KEY = "ai"
MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
DEFAULT_MODEL = "claude-opus-5-5"
# Claude Haiku 5.5 has no server-side fallback; the others retry a declined request on another model.
WITH_FALLBACK = {"claude-opus-5-5", "claude-sonnet-5-5"}
PROMPT_VERSION = "1"
TIMEOUT_SECONDS = 30.0

SYSTEM = (
    "You explain Azure access-review advice to people who are not cloud specialists. "
    "You receive, as JSON, facts about one grant of access (or about one identity's grants) and the advice "
    "a fixed rulebook gave. Write two or three short sentences in plain English: what the access lets them "
    "do, why the advice is given, and what to do next. Use only the facts given: never invent names, "
    "numbers, dates or reasons. Plain text only: no markdown, no lists, no headings, no JSON keys."
)

_WHO = {"person": "a person", "group": "a group", "app": "an app", "other": "an identity"}
_KIND = {
    "User": "person",
    "Group": "group",
    "ForeignGroup": "group",
    "ServicePrincipal": "app",
    "MSI": "app",
    "Application": "app",
}
_LEVEL = {
    "access": "change who has access (it can give anyone, itself included, any power)",
    "keys": "read keys, secrets and passwords, and change resources",
    "change": "create, change and delete resources",
    "data": "read or change the data inside",
    "view": "only look at how things are set up",
    "unknown": "an unknown set of permissions",
}
_PLACE = {
    "mgmt_group": "management group",
    "subscription": "subscription",
    "resource_group": "resource group",
    "resource": "resource",
}


class AiUnavailable(Exception):
    """AI wording is off, or has no key: nothing was sent."""


class AiFailed(Exception):
    """The call was made and failed, or Claude declined; the message says which, in words."""


@dataclass
class AiSettings:
    enabled: bool
    model: str
    key_encrypted: str | None


def load_settings(session: Session) -> AiSettings:
    v = (row.value or {}) if (row := session.get(AppSetting, SETTINGS_KEY)) else {}
    model = v.get("model") if v.get("model") in MODELS else DEFAULT_MODEL
    return AiSettings(
        enabled=bool(v.get("enabled")), model=model, key_encrypted=v.get("key_encrypted")
    )


def key_source(s: AiSettings) -> str | None:
    if s.key_encrypted:
        return "stored"
    return "environment" if os.environ.get("ANTHROPIC_API_KEY") else None


def save_settings(session: Session, enabled: bool, model: str, api_key: str | None) -> AiSettings:
    """``api_key``: None keeps the stored key, "" clears it, anything else replaces it. The caller commits.

    Raises ``ValueError`` for an unknown model, or a key that can't be stored encrypted.
    """
    if model not in MODELS:
        raise ValueError(f"model must be one of {', '.join(MODELS)}")
    cur = load_settings(session)
    key = cur.key_encrypted
    if api_key is not None:
        api_key = api_key.strip()
        if not api_key:
            key = None
        else:
            if not encryption_available():
                raise ValueError("set ENCRYPTION_KEY first: the API key is only stored encrypted")
            token = encrypt(api_key)
            key = token.decode() if token else None
    value = {"enabled": bool(enabled), "model": model, "key_encrypted": key}
    row = session.get(AppSetting, SETTINGS_KEY)
    if row is None:
        session.add(AppSetting(key=SETTINGS_KEY, value=value))
    else:
        row.value = value
    return AiSettings(enabled=value["enabled"], model=model, key_encrypted=key)


def _api_key(s: AiSettings) -> str | None:
    if s.key_encrypted:
        return decrypt(s.key_encrypted.encode())
    return os.environ.get("ANTHROPIC_API_KEY") or None


def available(session: Session) -> bool:
    s = load_settings(session)
    return s.enabled and key_source(s) is not None


# ---- what is sent ------------------------------------------------------------------------


def _who(ra: RoleAssignment, adv: Advice) -> str:
    if adv.rule == "R16 microsoft-app":
        return "an app Microsoft runs for an Azure service"
    if "#ext#" in (ra.principal_name or "").lower():
        return "a guest from another organisation"
    return _WHO[_KIND.get(ra.principal_type, "other")]


def _how(ra: RoleAssignment, now: datetime) -> str:
    if ra.assignment_type == "Activated":
        return "switched on for a while through PIM"
    if ra.end_at is not None:
        end = as_utc(ra.end_at)
        return f"time-bound, ends in {max(0, (end - now).days)} days"
    return "standing: always on"


def grant_facts(ctx: AdviceContext, ra: RoleAssignment, adv: Advice) -> dict[str, Any]:
    """The facts sent for one grant: no person's name, UPN or object id."""
    scope = ra.scope.lower()
    place = ctx.view.name.get(scope) or ra.scope_name or scope.rstrip("/").rsplit("/", 1)[-1]
    created = as_utc(ra.created_on)
    return {
        "who": _who(ra, adv),
        "role": ra.role_name or "an unnamed role",
        "custom_role": ra.role_type == "CustomRole",
        "it_lets_them": _LEVEL.get(ctx.view.capability_of(ra).level, _LEVEL["unknown"]),
        "where": f"the {_PLACE.get(ra.scope_level, 'place')} {place}",
        "applies_to_everything_inside": ra.scope_level != "resource",
        "how": _how(ra, ctx.now),
        "limited_by_a_condition": bool(ra.condition),
        "days_since_given": (ctx.now - created).days if created else None,
        "giver_known": bool(ra.created_by),
        "advice": {"verdict": adv.title, "weight": adv.weight, "steps": adv.how},
        "also_noted": [n.title for n in adv.also],
    }


def identity_facts(kind: str, advice: IdentityAdvice) -> dict[str, Any]:
    """The facts sent for one identity's "What to do": no name, UPN or object id."""
    return {
        "who": _WHO.get(kind, "an identity"),
        "grants_with_nothing_to_do": advice.fine,
        "actions": [
            {
                "verdict": a.title,
                "weight": a.weight,
                "grants": [
                    f"{g.role_name or 'a role'} on the {_PLACE.get(g.scope_level, 'place')} "
                    f"{g.scope_name or g.scope.rsplit('/', 1)[-1]}"
                    for g in a.grants[:8]
                ],
                "more_grants": max(0, len(a.grants) - 8),
                "rule_explains": RULES[a.rule].title,
            }
            for a in advice.actions
        ],
        "more_kinds_of_action": advice.more,
    }


# ---- the call --------------------------------------------------------------------------------


def _hash(model: str, facts: dict[str, Any]) -> str:
    raw = json.dumps(
        {"v": PROMPT_VERSION, "model": model, "facts": facts}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _client(api_key: str) -> Any:
    import anthropic  # imported only when AI wording is on and used

    return anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=2)


def written_today(session: Session, now: datetime | None = None) -> int:
    """New texts written since midnight UTC: what the daily limit counts."""
    midnight = (
        (now or datetime.now(UTC))
        .astimezone(UTC)
        .replace(hour=0, minute=0, second=0, microsecond=0)
    )
    return (
        session.scalar(
            select(func.count()).select_from(AiText).where(AiText.created_at >= midnight)
        )
        or 0
    )


def explain(
    session: Session, facts: dict[str, Any], now: datetime | None = None
) -> tuple[str, str, bool]:
    """``(text, model, cached)``. Raises ``AiUnavailable`` (nothing sent) or ``AiFailed``. The caller commits."""
    s = load_settings(session)
    if not s.enabled:
        raise AiUnavailable("AI wording is off (Settings › Advice)")
    key = _api_key(s)
    if not key:
        raise AiUnavailable("no API key: add one in Settings › Advice")
    digest = _hash(s.model, facts)
    if (hit := session.get(AiText, digest)) is not None:
        return hit.text, hit.model, True
    limit = settings.ai_daily_limit
    if limit > 0 and written_today(session, now) >= limit:
        raise AiUnavailable(
            f"today's limit of {limit} new texts is reached (AI_DAILY_LIMIT); texts already written still show. "
            "It resets at midnight UTC."
        )

    import anthropic

    request: dict[str, Any] = {
        "model": s.model,
        "max_tokens": 16000,
        "system": SYSTEM,
        "messages": [
            {"role": "user", "content": json.dumps(facts, sort_keys=True, ensure_ascii=False)}
        ],
        "output_config": {"effort": "low"},  # a short rewrite: thinking stays light
    }
    client = _client(key)
    try:
        if s.model in WITH_FALLBACK:
            response = client.beta.messages.create(
                **request, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
            )
        else:
            response = client.messages.create(**request)
    except anthropic.AuthenticationError as exc:
        raise AiFailed("the API key was refused") from exc
    except anthropic.PermissionDeniedError as exc:
        raise AiFailed("the API key may not use this model") from exc
    except anthropic.RateLimitError as exc:
        raise AiFailed("too many requests just now: try again in a minute") from exc
    except anthropic.APIStatusError as exc:
        raise AiFailed(f"the AI service answered with an error ({exc.status_code})") from exc
    except anthropic.APIConnectionError as exc:  # also covers timeouts
        raise AiFailed("could not reach the AI service") from exc

    if response.stop_reason == "refusal":
        raise AiFailed(
            "the AI service declined to word this one; the rule text above still applies"
        )
    text = " ".join(b.text.strip() for b in response.content if b.type == "text").strip()
    if not text:
        raise AiFailed("the AI service returned no text")
    session.add(
        AiText(
            hash=digest,
            model=response.model or s.model,
            text=text,
            prompt_version=PROMPT_VERSION,
            created_at=now or datetime.now(UTC),
        )
    )
    return text, response.model or s.model, False
