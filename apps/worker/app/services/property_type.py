"""Property type (``deals.service``) — FON-115, Sam 2026-10-20.

One vocabulary, the standard hotel-RE property-type taxonomy:

    Full-Service · Select-Service · Limited-Service · Extended-Stay · Resort

"Luxury" and "Lifestyle" are NOT property types — they are brand-style /
positioning concepts carried by ``deals.positioning`` (STR chain scale) and
``deals.brand``. Having "Luxury" mean two different things in two dropdowns
was the confusion this taxonomy removes.

How ``deals.service`` gets a value:

* **Only from the Offering Memorandum.** ``property_overview.property_type``
  is normalised by :func:`normalize_property_type`; a value that maps
  confidently to one of the five lands on the deal (fill-only — see
  ``api/documents._sync_deal_metadata_from_extraction``). Anything that does
  not map leaves ``service`` NULL. There is no wizard picker and no default:
  we never guess a property type.
* **API callers** (``POST`` / ``PATCH /deals``) may set it explicitly, but
  only to one of the five values — spelled loosely ("full service",
  "FULL-SERVICE") but unambiguously. Anything else is 422.

Readers: the comp-sales matcher's subject chain scale falls back to
:func:`chain_scale_for_property_type` when the deal has no positioning tier
(``api/deals._chain_scale_for_deal_row``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

FULL_SERVICE = "Full-Service"
SELECT_SERVICE = "Select-Service"
LIMITED_SERVICE = "Limited-Service"
EXTENDED_STAY = "Extended-Stay"
RESORT = "Resort"

PROPERTY_TYPES: tuple[str, ...] = (
    FULL_SERVICE,
    SELECT_SERVICE,
    LIMITED_SERVICE,
    EXTENDED_STAY,
    RESORT,
)

# Phrases that confidently mean one of the five. Matched on a lower-cased,
# whitespace/hyphen-normalised copy of the text, as whole words, so
# "full-service", "Full Service" and "fullservice" all read as the same
# token. Style words ("boutique", "lifestyle", "luxury", "upscale", …) are
# simply not in this table: they neither match nor block a match.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    FULL_SERVICE: ("full service", "fullservice", "full svc"),
    SELECT_SERVICE: ("select service", "selectservice", "focused service", "focused-service"),
    LIMITED_SERVICE: ("limited service", "limitedservice"),
    EXTENDED_STAY: ("extended stay", "extendedstay", "long stay", "long-stay", "all suite extended stay"),
    RESORT: ("resort",),
}

# ``service`` → approximate STR chain-scale bucket, used ONLY when the deal
# has no positioning tier (the analyst's / the OM brand's chain scale always
# wins). Extended-Stay brands span economy to upscale, so no single bucket is
# honest for it — it maps to nothing rather than to a guess.
_CHAIN_SCALE_FALLBACK: dict[str, str | None] = {
    FULL_SERVICE: "upper-upscale",
    RESORT: "upper-upscale",
    SELECT_SERVICE: "upper-midscale",
    LIMITED_SERVICE: "midscale",
    EXTENDED_STAY: None,
}


@dataclass(frozen=True)
class PropertyTypeMatch:
    value: str
    matched: str  # the synonym that matched, for audit detail


def _canon(text: str) -> str:
    t = text.strip().lower().replace("_", " ").replace("/", " ")
    t = re.sub(r"[‐‑‒–—-]+", " ", t)  # every dash → space
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def match_property_type(raw: object) -> PropertyTypeMatch | None:
    """The one property type ``raw`` names, or ``None``.

    Rules (deliberately conservative — a wrong type is worse than none):

    * Whole-word synonym match per value on the normalised text.
    * ``Resort`` wins over a service-level token in the same phrase
      ("Full-Service Resort" is a resort).
    * Two different service levels in one phrase ("select or full service")
      is ambiguous → ``None``.
    * Style-only text ("Luxury", "Boutique Lifestyle") → ``None``.
    """
    if raw is None:
        return None
    text = _canon(str(raw))
    if not text:
        return None
    padded = f" {text} "
    hits: dict[str, str] = {}
    for value, synonyms in _SYNONYMS.items():
        for syn in synonyms:
            s = _canon(syn)
            if f" {s} " in padded:
                hits[value] = syn
                break
    if not hits:
        return None
    if RESORT in hits:
        return PropertyTypeMatch(RESORT, hits[RESORT])
    if len(hits) > 1:
        return None
    value, syn = next(iter(hits.items()))
    # A second service-level word outside the matched phrase ("select or
    # full service", "limited/full service") makes the read ambiguous.
    words = set(text.split())
    stray = {
        FULL_SERVICE: {"select", "limited", "extended", "focused"},
        SELECT_SERVICE: {"full", "limited", "extended"},
        LIMITED_SERVICE: {"full", "select", "extended", "focused"},
        EXTENDED_STAY: {"full", "select", "limited", "focused"},
    }[value]
    if words & stray:
        return None
    return PropertyTypeMatch(value, syn)


def normalize_property_type(raw: object) -> str | None:
    """:func:`match_property_type` reduced to the stored value."""
    m = match_property_type(raw)
    return m.value if m else None


def validate_property_type_input(raw: str | None) -> str | None:
    """For API bodies: ``None`` passes through; a string must name exactly
    one of the five (spelling tolerant) or ``ValueError`` is raised."""
    if raw is None:
        return None
    if not str(raw).strip():
        return None
    m = match_property_type(raw)
    if m is None:
        raise ValueError(
            f"service must be one of {', '.join(PROPERTY_TYPES)}; got {raw!r}"
        )
    return m.value


def chain_scale_for_property_type(service: object) -> str | None:
    """Approximate chain-scale bucket for a stored property type (fallback
    only — see module docstring). Unknown / NULL → ``None``."""
    value = normalize_property_type(service)
    if value is None:
        return None
    return _CHAIN_SCALE_FALLBACK.get(value)


__all__ = [
    "EXTENDED_STAY",
    "FULL_SERVICE",
    "LIMITED_SERVICE",
    "PROPERTY_TYPES",
    "PropertyTypeMatch",
    "RESORT",
    "SELECT_SERVICE",
    "chain_scale_for_property_type",
    "match_property_type",
    "normalize_property_type",
    "validate_property_type_input",
]
