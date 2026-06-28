"""LOI draft generator — fill a hospitality Letter of Intent template.

Last artifact every IC committee asks for: "draft an LOI so we can
send it Monday". This module fills a standard hospitality LOI template
with deal metadata + the max-price-for-target-IRR scalar from
``price_solver``. No LLM call — just templated markdown.

The template is the lowest-common-denominator hospitality LOI body:
buyer/seller/asset/price/earnest-money/deposit-at-PA/DD/closing/
financing contingency/exclusivity/representation/validity/contingencies.
That covers ~80% of what a buy-side broker would put in a "first offer"
LOI; the analyst will edit the final 20% by hand (boilerplate language,
tenant subordination, brand-approval mechanics).

The output dataclass carries both the structured fields (for downstream
edit-in-place UI) and a pre-rendered ``rendered_markdown`` body (for
direct copy-paste to email / Word). The two stay in sync because the
markdown is rebuilt from the dataclass at draft time — never edit one
without the other.

Wave 2 P2.8.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .price_solver import MaxPriceResult


# ─────────────────────────── defaults ──────────────────────────────


# Standard hospitality LOI terms — these are the numbers Sam (and most
# institutional buyers) anchor to. Analyst can override per-deal via
# the API; defaults make the "fire-and-forget" path one click.
DEFAULT_EARNEST_MONEY_PCT: float = 0.01            # 1% of purchase
DEFAULT_DEPOSIT_AT_PA_PCT: float = 0.02            # 2% of purchase at PA
DEFAULT_DUE_DILIGENCE_DAYS: int = 30
DEFAULT_CLOSING_DAYS_FROM_PA: int = 60
DEFAULT_FINANCING_CONTINGENCY: str = "60 days from PA execution"
DEFAULT_EXCLUSIVITY_DAYS: int = 21
DEFAULT_VALID_UNTIL: str = "10 business days from issuance"

DEFAULT_CONTINGENCIES: tuple[str, ...] = (
    "Satisfactory Phase I ESA",
    "Satisfactory PIP estimate",
    "Title commitment review",
)


# ─────────────────────────── dataclass ──────────────────────────────


@dataclass
class LOIDraft:
    """Structured + rendered LOI draft.

    The structured fields drive the rendered_markdown — call
    :func:`draft_loi` to regenerate from a mutated dataclass (the UI
    edit-in-place flow goes through the API which re-renders).
    """

    asset_name: str
    asset_address: str
    rooms: int
    proposed_price: float
    proposed_price_per_key: float
    buyer: str = "[Buyer Entity TBD]"
    seller: str = "[Seller TBD]"
    earnest_money_pct: float = DEFAULT_EARNEST_MONEY_PCT
    deposit_at_pa: float = 0.0      # absolute $ — caller fills
    due_diligence_days: int = DEFAULT_DUE_DILIGENCE_DAYS
    closing_days_from_pa: int = DEFAULT_CLOSING_DAYS_FROM_PA
    financing_contingency: str = DEFAULT_FINANCING_CONTINGENCY
    exclusivity_days: int = DEFAULT_EXCLUSIVITY_DAYS
    representation: str = "[Buyer Broker]"
    valid_until: str = DEFAULT_VALID_UNTIL
    contingencies: list[str] = field(
        default_factory=lambda: list(DEFAULT_CONTINGENCIES)
    )
    rendered_markdown: str = ""


# ─────────────────────────── helpers ────────────────────────────────


def _format_usd(amount: float) -> str:
    """`$42,800,000` style — round to nearest dollar; thousands separators."""
    return f"${amount:,.0f}"


def _format_pct(pct: float, *, decimals: int = 1) -> str:
    return f"{pct * 100:.{decimals}f}%"


def _render_markdown(draft: LOIDraft) -> str:
    """Hospitality LOI body — ~50 lines of standard institutional language.

    Layout (matches the institutional buy-side broker template):

    1. Header (parties, date, salutation)
    2. Property + purchase price
    3. Earnest money + deposit terms
    4. Due diligence + financing
    5. Closing terms
    6. Exclusivity
    7. Representation
    8. Contingencies (bulleted list)
    9. Validity + signature blocks
    """
    contingencies_block = "\n".join(
        f"   - {c}" for c in draft.contingencies
    )
    earnest_money_usd = draft.proposed_price * draft.earnest_money_pct

    body = f"""# LETTER OF INTENT

**To:** {draft.seller}
**From:** {draft.buyer}
**Re:** Proposed Acquisition of {draft.asset_name}
**Property Address:** {draft.asset_address}

This Letter of Intent ("**LOI**") sets forth the principal terms upon
which {draft.buyer} ("**Buyer**") proposes to acquire {draft.asset_name}
("**Property**") from {draft.seller} ("**Seller**"). This LOI is
non-binding except as expressly stated below.

## 1. Property

The Property consists of the {draft.asset_name} hotel located at
{draft.asset_address}, comprising {draft.rooms:,} guest rooms (the
"**Property**"), together with all related fixtures, FF&E, intangibles,
operating licenses, and all books and records relating to the operation
of the Property.

## 2. Purchase Price

Buyer offers a purchase price of **{_format_usd(draft.proposed_price)}**
({_format_usd(draft.proposed_price_per_key)} per key), payable in cash
at closing, subject to customary closing prorations and adjustments.

## 3. Earnest Money

Within three (3) business days of mutual execution of a Purchase and
Sale Agreement ("**PA**"), Buyer will deposit earnest money equal to
{_format_pct(draft.earnest_money_pct)} of the purchase price
({_format_usd(earnest_money_usd)}) into a mutually agreeable escrow
account. An additional deposit of {_format_usd(draft.deposit_at_pa)}
will be funded upon expiration of the Due Diligence Period defined
below.

## 4. Due Diligence

Buyer will have {draft.due_diligence_days} days from PA execution to
conduct customary due diligence ("**DD Period**"), including without
limitation: financial, physical, environmental, operational, brand,
title, survey, zoning and entitlement review. Buyer may terminate the
PA for any reason during the DD Period and recover its earnest money
deposit.

## 5. Financing

Buyer's obligation to close is contingent upon obtaining acquisition
financing on terms reasonably acceptable to Buyer within
{draft.financing_contingency}.

## 6. Closing

Closing shall occur on or before {draft.closing_days_from_pa} days
following PA execution, subject to satisfaction or waiver of all
closing conditions.

## 7. Exclusivity

Upon execution of this LOI, Seller agrees to negotiate exclusively with
Buyer for a period of {draft.exclusivity_days} days during which Seller
will not solicit, accept, or entertain any third-party offers,
indications of interest, or related discussions with respect to the
Property.

## 8. Representation

Buyer is represented by {draft.representation}. Seller represents that
no broker, finder, or other party is entitled to a commission or
finder's fee in connection with this transaction except as may be
disclosed separately in writing.

## 9. Contingencies

Buyer's obligation to close is further contingent upon:
{contingencies_block}

## 10. Confidentiality and Non-Binding Effect

Except for the Exclusivity provision in Section 7 and this Section 10,
this LOI is non-binding and is intended solely to outline the principal
terms upon which the parties propose to proceed. The terms of the
transaction shall be set forth in a definitive PA negotiated by the
parties in good faith.

## 11. Validity

This offer is valid until **{draft.valid_until}** and will expire
automatically thereafter unless extended in writing by Buyer.

---

Sincerely,

{draft.buyer}

____________________________
Authorized Signatory

---

**ACCEPTED AND AGREED:**

{draft.seller}

____________________________
Authorized Signatory
"""
    return body


# ─────────────────────────── public entrypoint ─────────────────────


def draft_loi(
    *,
    asset_name: str,
    asset_address: str,
    rooms: int,
    max_price_result: MaxPriceResult,
    buyer: str | None = None,
    seller: str | None = None,
    earnest_money_pct: float = DEFAULT_EARNEST_MONEY_PCT,
    due_diligence_days: int = DEFAULT_DUE_DILIGENCE_DAYS,
    closing_days_from_pa: int = DEFAULT_CLOSING_DAYS_FROM_PA,
    financing_contingency: str = DEFAULT_FINANCING_CONTINGENCY,
    exclusivity_days: int = DEFAULT_EXCLUSIVITY_DAYS,
    representation: str | None = None,
    valid_until: str = DEFAULT_VALID_UNTIL,
    contingencies: list[str] | None = None,
    proposed_price_override: float | None = None,
) -> LOIDraft:
    """Build an :class:`LOIDraft` from deal facts + a max-price result.

    Defaults the proposed price to ``max_price_result.max_price_for_irr``
    — institutional buyers price to a target IRR, not a target EM. The
    analyst can override with ``proposed_price_override`` when they want
    to offer below the cap (negotiation strategy: leave room to come up).

    All optional fields fall back to the module-level defaults defined
    at the top of this module. The dataclass is fully serialisable so
    the API layer can ship it straight to JSON; ``rendered_markdown`` is
    the copy-paste-ready body.
    """
    proposed_price = (
        proposed_price_override
        if proposed_price_override is not None
        else max_price_result.max_price_for_irr
    )
    proposed_price_per_key = proposed_price / rooms if rooms > 0 else 0.0
    deposit_at_pa = proposed_price * DEFAULT_DEPOSIT_AT_PA_PCT

    draft = LOIDraft(
        asset_name=asset_name,
        asset_address=asset_address,
        rooms=rooms,
        proposed_price=proposed_price,
        proposed_price_per_key=proposed_price_per_key,
        buyer=buyer or "[Buyer Entity TBD]",
        seller=seller or "[Seller TBD]",
        earnest_money_pct=earnest_money_pct,
        deposit_at_pa=deposit_at_pa,
        due_diligence_days=due_diligence_days,
        closing_days_from_pa=closing_days_from_pa,
        financing_contingency=financing_contingency,
        exclusivity_days=exclusivity_days,
        representation=representation or "[Buyer Broker]",
        valid_until=valid_until,
        contingencies=(
            list(contingencies)
            if contingencies is not None
            else list(DEFAULT_CONTINGENCIES)
        ),
    )
    draft.rendered_markdown = _render_markdown(draft)
    return draft


__all__ = [
    "DEFAULT_CLOSING_DAYS_FROM_PA",
    "DEFAULT_CONTINGENCIES",
    "DEFAULT_DEPOSIT_AT_PA_PCT",
    "DEFAULT_DUE_DILIGENCE_DAYS",
    "DEFAULT_EARNEST_MONEY_PCT",
    "DEFAULT_EXCLUSIVITY_DAYS",
    "DEFAULT_FINANCING_CONTINGENCY",
    "DEFAULT_VALID_UNTIL",
    "LOIDraft",
    "draft_loi",
]
