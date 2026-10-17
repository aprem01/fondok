"""OM brand → STR chain scale (Sam, 2026-10-15).

    "If we can confidently identify the brand from the OM and map it to STR,
    its chain scale should populate automatically as the suggested
    positioning. The analyst should still be able to override the value,
    and the source should remain visible."

The catalog is the web's brand catalog (``apps/web/src/lib/mockData.ts``
``brandFamilies``), exported by ``scripts/export_brand_catalog.mjs`` to
``app/data/brand_chain_scales.json`` (a web test fails when the two drift).
Each brand carries its name, explicit aliases, and its STR chain scale where
STR publishes one (STR Chain Scales, February 2026). The catalog's own
``tier`` is NOT exported: the suggestion is STR's scale or nothing.

**Confidence rule** — a brand string from the OM resolves to a catalog brand
only by one of these deterministic routes, never by a similarity score:

1. *exact* — the normalised string equals a brand's normalised name;
2. *alias* — it equals one of the brand's explicit aliases;
3. *short form* — it equals the brand's name with the parent-chain tail and
   generic suffixes dropped ("Kimpton" for "Kimpton Hotels & Restaurants",
   "Hampton" for "Hampton by Hilton", "Westin" for "The Westin Hotels &
   Resorts");
4. *leading brand mark* — the string STARTS with one of those keys as whole
   words and the rest is a property name ("Kimpton Angler's" → Kimpton). The
   longest leading key wins, and the match is refused when any remaining
   word is itself part of some catalog brand key ("Marriott Courtyard" could
   be Marriott Hotels or Courtyard — refused) apart from the generic words
   "hotel", "resort", "inn", "suites", "the", ….

Normalisation is case-, accent- and punctuation-insensitive (apostrophes
dropped, "&" read as "and"). A key shared by more than one brand (the
"Starwood" alias on a dozen legacy Starwood brands) is *ambiguous* and
resolves to nothing. A brand STR does not publish a single scale for
("Various": Independent, Design Hotels, the Outdoor Collection; or no STR
row at all) is matched but yields no suggestion.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "brand_chain_scales.json"

#: ``deals.positioning_source`` values.
POSITIONING_SOURCE_OM_BRAND = "om_brand_str_scale"
POSITIONING_SOURCE_ANALYST = "analyst"

MatchKind = Literal["exact", "alias", "short_form", "leading_brand_mark"]
MatchStatus = Literal["matched", "ambiguous", "no_match", "no_str_scale"]

# Generic words that never distinguish one brand from another, so they may
# trail a leading brand mark ("Kimpton Angler's Hotel", "Hampton Inn &
# Suites") without refusing it.
_GENERIC_WORDS: frozenset[str] = frozenset(
    {"hotel", "hotels", "resort", "resorts", "inn", "suites", "the", "and",
     "by", "a", "an", "at", "of", "on", "in", "spa"}
)

# Trailing phrases dropped to form a brand's short form (applied after the
# " by <parent>" tail is removed), longest first.
_SHORT_FORM_SUFFIXES: tuple[str, ...] = (
    "hotels and restaurants",
    "hotels and resorts",
    "resorts and spas",
    "hotels and co",
    "hotels",
    "hotel",
    "resorts",
)


def normalize_brand(value: object) -> str:
    """Case / accent / punctuation-insensitive form used for every comparison."""
    if not isinstance(value, str):
        return ""
    s = unicodedata.normalize("NFKD", value)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("’", "").replace("'", "").replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _short_form(normalized_name: str) -> str | None:
    s = normalized_name
    if " by " in s:
        s = s.split(" by ", 1)[0].strip()
    for suffix in _SHORT_FORM_SUFFIXES:
        if s.endswith(" " + suffix):
            s = s[: -len(suffix) - 1].strip()
            break
    if s.startswith("the "):
        s = s[4:].strip()
    if not s or s in _GENERIC_WORDS or s == normalized_name:
        return None
    return s


@dataclass(frozen=True)
class CatalogBrand:
    name: str
    family: str
    str_scale: str | None
    positioning: str | None


@dataclass(frozen=True)
class BrandScaleMatch:
    """Outcome of resolving one OM brand string."""

    status: MatchStatus
    input: str
    brand: CatalogBrand | None = None
    kind: MatchKind | None = None
    matched_key: str | None = None
    candidates: tuple[str, ...] = ()

    @property
    def suggested_positioning(self) -> str | None:
        """The ``positioningTiers`` id to suggest — only on a confident match
        to a brand with a published STR chain scale."""
        if self.status != "matched" or self.brand is None:
            return None
        return self.brand.positioning


@dataclass(frozen=True)
class _Catalog:
    # normalised key → {brand name: (CatalogBrand, kind)}
    keys: dict[str, dict[str, tuple[CatalogBrand, MatchKind]]]
    vocabulary: frozenset[str]


def _load_catalog_doc(path: Path = CATALOG_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def _catalog() -> _Catalog:
    doc = _load_catalog_doc()
    keys: dict[str, dict[str, tuple[CatalogBrand, MatchKind]]] = {}

    def add(key: str, brand: CatalogBrand, kind: MatchKind) -> None:
        if not key:
            return
        bucket = keys.setdefault(key, {})
        # The strongest route per brand wins (exact > alias > short form).
        if brand.name not in bucket:
            bucket[brand.name] = (brand, kind)

    for row in doc.get("brands", []):
        brand = CatalogBrand(
            name=str(row["name"]),
            family=str(row.get("family") or ""),
            str_scale=row.get("str_scale"),
            positioning=row.get("positioning"),
        )
        norm = normalize_brand(brand.name)
        add(norm, brand, "exact")
        for alias in row.get("aliases") or []:
            add(normalize_brand(alias), brand, "alias")
        short = _short_form(norm)
        if short:
            add(short, brand, "short_form")

    vocabulary = frozenset(
        w for k in keys for w in k.split() if w not in _GENERIC_WORDS
    )
    return _Catalog(keys=keys, vocabulary=vocabulary)


def _resolve_key(
    raw: str, key: str, kind_override: MatchKind | None = None
) -> BrandScaleMatch:
    bucket = _catalog().keys[key]
    if len(bucket) > 1:
        return BrandScaleMatch(
            status="ambiguous",
            input=raw,
            matched_key=key,
            candidates=tuple(sorted(bucket)),
        )
    brand, kind = next(iter(bucket.values()))
    return BrandScaleMatch(
        status="matched" if brand.positioning else "no_str_scale",
        input=raw,
        brand=brand,
        kind=kind_override or kind,
        matched_key=key,
        candidates=(brand.name,),
    )


def match_brand(value: object) -> BrandScaleMatch:
    """Resolve an OM brand string under the confidence rule (module doc)."""
    raw = value if isinstance(value, str) else ""
    norm = normalize_brand(raw)
    if not norm:
        return BrandScaleMatch(status="no_match", input=raw)
    cat = _catalog()
    if norm in cat.keys:
        return _resolve_key(raw, norm)

    # Leading brand mark: the longest catalog key that is a whole-word prefix.
    words = norm.split()
    for n in range(len(words) - 1, 0, -1):
        key = " ".join(words[:n])
        if key not in cat.keys:
            continue
        rest = [w for w in words[n:] if w not in _GENERIC_WORDS]
        clashing = sorted({w for w in rest if w in cat.vocabulary})
        if clashing:
            # Another brand's word follows the mark ("Marriott Courtyard"):
            # which brand is meant is a guess, so nothing is suggested.
            return BrandScaleMatch(
                status="ambiguous",
                input=raw,
                matched_key=key,
                candidates=tuple(sorted(cat.keys[key])),
            )
        return _resolve_key(raw, key, kind_override="leading_brand_mark")
    return BrandScaleMatch(status="no_match", input=raw)


def catalog_brand_count() -> int:
    return len(_load_catalog_doc().get("brands", []))
