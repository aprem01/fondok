"""The worker's default CORS origin regex — which browser origins may call it.

Both Vercel projects deploy the same web app: the original one in
``aprem01s-projects`` (aliased fondok-app.vercel.app) and, since 2026-10-22,
the Fondok-Final one in the ``fondok`` team (fondok-drab.vercel.app /
fondok-fondok.vercel.app). The regex must admit both and nothing that merely
starts with "fondok".
"""

from __future__ import annotations

import re

import pytest

from app.config import Settings


@pytest.fixture
def pattern() -> re.Pattern[str]:
    return re.compile(Settings.model_fields["ALLOWED_CORS_ORIGIN_REGEX"].default)


@pytest.mark.parametrize(
    "origin",
    [
        "https://fondok-i7hxatoqj-aprem01s-projects.vercel.app",
        "https://fondok-qellk6mjk-fondok.vercel.app",
        "https://fondok-drab.vercel.app",
        "https://fondok-fondok.vercel.app",
    ],
)
def test_allowed_origins(pattern, origin):
    assert pattern.fullmatch(origin)


@pytest.mark.parametrize(
    "origin",
    [
        "http://fondok-drab.vercel.app",  # not https
        "https://fondok-drab.vercel.app.evil.com",
        "https://evil-fondok-drab.vercel.app",
        "https://fondok-xyz-someone-else.vercel.app",
        "https://fondok.vercel.app",
    ],
)
def test_rejected_origins(pattern, origin):
    assert not pattern.fullmatch(origin)
