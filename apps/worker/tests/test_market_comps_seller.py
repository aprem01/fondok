"""FON-72 — the Market Tab transaction-comps surface carries a SELLER column.

Light-weight unit coverage for the additive ``seller`` field on
``app.api.market``: the model accepts it, and the extractor alias map routes
the common seller field names onto it. (The full endpoint materialization is
exercised via the tenant-isolation ASGI suite.)
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from app.api.market import (  # noqa: E402
    _TXN_FIELD_ALIASES,
    TransactionCompEntry,
)


def test_entry_accepts_seller() -> None:
    entry = TransactionCompEntry(name="Test Hotel", seller="Host Hotels")
    assert entry.seller == "Host Hotels"
    # Absent → null (renders "—"), never a missing attribute.
    assert TransactionCompEntry(name="Other").seller is None


def test_seller_aliases_route_to_seller() -> None:
    for alias in ("seller", "seller_name", "vendor", "disposition_by"):
        assert _TXN_FIELD_ALIASES[alias] == "seller"
    # Buyer aliases are untouched.
    assert _TXN_FIELD_ALIASES["buyer"] == "buyer_name"
