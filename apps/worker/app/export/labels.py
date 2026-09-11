"""Canonical NOI row labels shared by the export builders.

The bare word "NOI" means NOI **before** the FF&E replacement reserve
(founder decision — FON-59 #1 / FON-67 #2): the engine field
``expense.years[].noi_institutional`` / registry concept ``ebitda``. The
after-reserve figure ``expense.years[].noi`` (registry concept ``noi``) is
"Cash NOI"; Debt uses it for DSCR / debt yield and Returns capitalises it at
the exit cap.

The proforma carries BOTH rows so the workbook foots (FON-54 #8):

    Total Revenue less Operating Expenses less the Management Fee
        = NOI (before FF&E reserve)
        less the FF&E Reserve
        = Cash NOI (after FF&E reserve)

These are display strings only — nothing here changes an engine value.
"""

from __future__ import annotations

#: NOI before the FF&E reserve — the headline / entry-cap basis.
NOI_BEFORE_RESERVE = "NOI (before FF&E reserve)"

#: NOI net of the FF&E reserve.
CASH_NOI = "Cash NOI (after FF&E reserve)"

#: Pre-upgrade ``engine_outputs`` rows persist ``noi_institutional: null``
#: (apps/worker/app/engines/expense.py). The value then falls back to ``noi``,
#: and the label must NOT assert "before FF&E reserve" about it.
NOI_BASIS_UNCONFIRMED = "NOI (basis unconfirmed — pre-upgrade run)"

#: The pre-FON-59 label, still emitted by :mod:`app.export.fixtures` and the
#: demo payload. Kept so a lookup written against the new vocabulary still
#: finds the headline NOI row on an older payload.
LEGACY_NOI = "Net Operating Income"

#: Every label that can carry the headline (before-reserve) NOI row, newest
#: first. Lookups over ``p_and_l_engine_proforma["lines"]`` should match this.
NOI_HEADLINE_LABELS: tuple[str, ...] = (
    NOI_BEFORE_RESERVE,
    NOI_BASIS_UNCONFIRMED,
    LEGACY_NOI,
)


def noi_before_reserve_label(*, basis_confirmed: bool) -> str:
    """The proforma row label for a before-reserve NOI series."""
    return NOI_BEFORE_RESERVE if basis_confirmed else NOI_BASIS_UNCONFIRMED
