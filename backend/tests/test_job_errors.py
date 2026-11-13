"""Stored job errors in plain words (core/job_errors.py)."""

from __future__ import annotations

import pytest

from app.core.job_errors import UNKNOWN, explain


@pytest.mark.parametrize(
    ("raw", "starts"),
    [
        # The outage of 9 November: a network cut that surfaces as a failed sign-in.
        (
            (
                "ClientAuthenticationError: Authentication failed: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred "
                "in violation of protocol (_ssl.c:1010)"
            ),
            "Could not reach Azure",
        ),
        (
            (
                "ScanInterrupted: could not read role assignments at /subscriptions/x (place 3 of 75): Azure did not "
                "answer (ServiceRequestError). Nothing was saved."
            ),
            "Could not reach Azure",
        ),
        (
            "GraphUnavailable: Microsoft Graph did not answer for names (ConnectError)",
            "Could not reach Azure",
        ),
        (
            "ClientAuthenticationError: AADSTS7000222: The provided client secret keys for app are expired.",
            "Sign-in to Azure failed: the client secret",
        ),
        (
            (
                "ClientAuthenticationError: DefaultAzureCredential failed to retrieve a token from the included "
                "credentials."
            ),
            "No Azure credentials are set",
        ),
        ("ClientAuthenticationError: AADSTS50034: something else", "Sign-in to Azure failed."),
        (
            "ScopeUnreachable: management group 'nope' was not found or is not readable",
            "The place does not exist",
        ),
        (
            "HttpResponseError: (AuthorizationFailed) The client does not have authorization",
            "The app's identity is not allowed",
        ),
        ("HttpResponseError: (TooManyRequests) throttled", "Azure asked to slow down"),
        (
            "Interrupted: the app restarted while this was running. It runs again by itself.",
            "Interrupted by a restart",
        ),
        ("Marked failed by sweeper: scan sent no heartbeat for > 3600s.", "It stopped responding"),
        (
            "HttpResponseError: The start time cannot be more than 90 days in the past",
            "Azure keeps the Activity Log for 90 days",
        ),
        # The morning of 11 November: the Mac had no DNS for a while.
        (
            (
                "ClientAuthenticationError: Authentication failed: HTTPSConnection(host='login.microsoftonline.com', "
                "port=443): Failed to resolve 'login.microsoftonline.com' ([Errno -5] No address associated with "
                "hostname)"
            ),
            "Could not reach Azure: this machine could not look up",
        ),
        (
            (
                "ServiceRequestError: HTTPSConnection(host='management.azure.com', port=443): Max retries exceeded "
                '(Caused by NameResolutionError("Name or service not known"))'
            ),
            "Could not reach Azure: this machine could not look up",
        ),
        ("KeyError: 'surprise'", UNKNOWN),
    ],
)
def test_explain(raw: str, starts: str):
    assert explain(raw).startswith(starts)


def test_no_error_no_sentence():
    assert explain(None) is None and explain("  ") is None
