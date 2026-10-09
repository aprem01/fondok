"""Pure helpers for the transaction-comps payload (R-064 / E-020).

* :func:`normalize_comp_interest` maps an extracted ``interest_type`` (or a
  boolean ``ground_lease`` / ``fee_simple`` flag) onto the two labels the
  Transaction Comps table shows. It never infers: anything that is not an
  explicit fee-simple / ground-lease statement returns ``None`` ("—").
* :func:`comp_cap_rate_range` derives the "Comps range" hint shown on
  Investment's Entry Cap Rate row from the comps that DISCLOSE a cap rate.
  Fewer than two disclosed cap rates is not a range — it returns ``None`` and
  the UI says "no comparable cap rates extracted".
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

CompInterest = Literal["Fee Simple", "Ground Lease"]

_FEE_SIMPLE = {"fee_simple", "fee simple", "fee-simple", "fee simple absolute"}
_GROUND_LEASE = {
    "ground_lease",
    "ground lease",
    "ground-lease",
    "ground leasehold",
    "leasehold (ground lease)",
    "leasehold - ground lease",
    "leasehold — ground lease",
    "subject to ground lease",
}

MIN_CAP_RATE_OBSERVATIONS = 2


def normalize_comp_interest(
    interest_type: object = None,
    *,
    ground_lease: object = None,
    fee_simple: object = None,
) -> CompInterest | None:
    """Explicit interest label for one comp, or ``None`` when not stated.

    Boolean flags only ever assert their own interest — ``ground_lease=False``
    is NOT read as "fee simple" (that would be an inference).
    """
    if isinstance(interest_type, str):
        key = " ".join(interest_type.strip().lower().split())
        if key in _FEE_SIMPLE:
            return "Fee Simple"
        if key in _GROUND_LEASE:
            return "Ground Lease"
    if _is_true(ground_lease):
        return "Ground Lease"
    if _is_true(fee_simple):
        return "Fee Simple"
    return None


def _is_true(v: object) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"true", "yes", "y"}
    return False


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def comp_cap_rate_range(
    cap_rates_pct: Iterable[float | None],
) -> dict[str, float | int] | None:
    """``{"low_pct", "high_pct", "median_pct", "n"}`` over disclosed cap rates.

    Inputs are percents (``7.25`` = 7.25%). ``None`` / non-positive values are
    comps that did not disclose a cap rate and are skipped. Returns ``None``
    when fewer than :data:`MIN_CAP_RATE_OBSERVATIONS` remain.
    """
    caps = [float(c) for c in cap_rates_pct if c is not None and float(c) > 0]
    if len(caps) < MIN_CAP_RATE_OBSERVATIONS:
        return None
    return {
        "low_pct": min(caps),
        "high_pct": max(caps),
        "median_pct": _median(caps),
        "n": len(caps),
    }
