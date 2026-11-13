"""Why a check or a collection failed, in plain words.

Jobs store the raw exception (``ClientAuthenticationError: … [SSL:
UNEXPECTED_EOF_WHILE_READING] …``). :func:`explain` turns the common ones
into one sentence an admin can act on; the raw text stays available behind
"Details". Rules are tried in order: the first whose pattern matches wins.
"""

from __future__ import annotations

import re

# (pattern, sentence). Network first: a failed sign-in caused by a network
# cut still says "Authentication failed".
_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), sentence)
    for pattern, sentence in (
        (
            r"^Interrupted: the app restarted",
            "Interrupted by a restart of the app; it runs again by itself.",
        ),
        (
            r"Marked failed by sweeper",
            "It stopped responding and was closed after an hour without a sign of life.",
        ),
        (
            (
                r"Failed to resolve|NameResolutionError|No address associated with hostname|"
                r"Name or service not known|Temporary failure in name resolution|getaddrinfo"
            ),
            "Could not reach Azure: this machine could not look up Azure's address (no network or DNS).",
        ),
        (
            (
                r"\bSSL\b|TLS|CERTIFICATE_VERIFY|ConnectError|ConnectTimeout|ReadTimeout|timed out|"
                r"ServiceRequestError|ServiceResponseError|Connection (refused|reset|aborted)|"
                r"ProxyError|"
                r"Max retries exceeded|GraphUnavailable|did not answer"
            ),
            "Could not reach Azure: a network, proxy or certificate problem on the way out.",
        ),
        (
            r"AADSTS7000222|AADSTS7000215|invalid_client|client secret.*(expired|invalid)",
            "Sign-in to Azure failed: the client secret is wrong or has expired.",
        ),
        (
            r"AADSTS700016|AADSTS90002|unauthorized_client|Tenant .* not found",
            "Sign-in to Azure failed: the tenant or client id is wrong.",
        ),
        (
            r"DefaultAzureCredential failed|EnvironmentCredential.*unavailable|no credentials?",
            "No Azure credentials are set: add them on Settings › Credentials or in the env file.",
        ),
        (r"ClientAuthenticationError|Authentication failed|AADSTS\d+", "Sign-in to Azure failed."),
        (
            r"TooManyRequests|\b429\b|throttl",
            "Azure asked to slow down (too many requests); the next try usually works.",
        ),
        (
            r"ScopeUnreachable",
            "The place does not exist, or the app's identity is not allowed to read it.",
        ),
        (
            r"AuthorizationFailed|does not have authorization|\b403\b|Forbidden",
            "The app's identity is not allowed to read this in Azure.",
        ),
        (
            r"cannot be more than 90 days",
            "Azure keeps the Activity Log for 90 days: the window started too far back.",
        ),
        (
            r"no subscriptions visible",
            "The app's identity sees no subscription: it has no role in Azure yet.",
        ),
        (
            r"\b50[0234]\b|InternalServerError|ServiceUnavailable|GatewayTimeout",
            "Azure answered with a server error; the next try usually works.",
        ),
    )
)
UNKNOWN = "It failed for a reason RBAC Lens does not recognise; see the details."


def explain(error: str | None) -> str | None:
    """One plain sentence for a stored job error; ``None`` when there is no error."""
    if not error or not error.strip():
        return None
    for pattern, sentence in _RULES:
        if pattern.search(error):
            return sentence
    return UNKNOWN
