"""FON-115 — property type (``deals.service``) taxonomy.

Sam 2026-10-20: adopt the standard hotel-RE property types — Full-Service,
Select-Service, Limited-Service, Extended-Stay, Resort. Luxury / Lifestyle are
positioning / brand concepts and drop out of ``service``. The value is
populated from the OM only and never guessed; API callers may set it but only
to one of the five; the comp-sales chain-scale fallback reads the same
vocabulary.
"""

from __future__ import annotations

import pytest

from app.services.property_type import (
    EXTENDED_STAY,
    FULL_SERVICE,
    LIMITED_SERVICE,
    PROPERTY_TYPES,
    RESORT,
    SELECT_SERVICE,
    chain_scale_for_property_type,
    match_property_type,
    normalize_property_type,
    validate_property_type_input,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        # the only value the live OM extractor has produced so far
        ("Boutique Lifestyle Full-Service", FULL_SERVICE),
        ("Full Service", FULL_SERVICE),
        ("full-service hotel", FULL_SERVICE),
        ("FULL_SERVICE", FULL_SERVICE),
        ("Upscale select-service", SELECT_SERVICE),
        ("Focused-service", SELECT_SERVICE),
        ("Limited Service / Economy", LIMITED_SERVICE),
        ("All-suite extended stay", EXTENDED_STAY),
        ("Extended-Stay", EXTENDED_STAY),
        ("Resort", RESORT),
        ("Luxury full-service resort", RESORT),  # resort wins over the service level
        ("Beachfront resort hotel", RESORT),
        # style words alone are not a type — never guess
        ("Luxury", None),
        ("Lifestyle", None),
        ("Boutique", None),
        ("Upper Upscale", None),
        ("", None),
        ("   ", None),
        (None, None),
        # two service levels in one phrase is ambiguous
        ("select or full service", None),
        # partial words never match
        ("fullness of service", None),
        ("resorting to", None),
    ],
)
def test_normalize_property_type(raw, expected):
    assert normalize_property_type(raw) == expected


def test_match_reports_the_synonym_for_audit():
    m = match_property_type("Boutique Lifestyle Full-Service")
    assert m is not None and m.value == FULL_SERVICE and m.matched == "full service"


def test_every_taxonomy_value_normalises_to_itself():
    for v in PROPERTY_TYPES:
        assert normalize_property_type(v) == v
        assert normalize_property_type(v.lower().replace("-", " ")) == v


def test_api_input_validation():
    assert validate_property_type_input(None) is None
    assert validate_property_type_input("") is None
    assert validate_property_type_input("full service") == FULL_SERVICE
    assert validate_property_type_input("RESORT") == RESORT
    with pytest.raises(ValueError):
        validate_property_type_input("Luxury")
    with pytest.raises(ValueError):
        validate_property_type_input("Lifestyle")
    with pytest.raises(ValueError):
        validate_property_type_input("hotel")


def test_chain_scale_fallback_vocabulary():
    assert chain_scale_for_property_type(FULL_SERVICE) == "upper-upscale"
    assert chain_scale_for_property_type(RESORT) == "upper-upscale"
    assert chain_scale_for_property_type(SELECT_SERVICE) == "upper-midscale"
    assert chain_scale_for_property_type(LIMITED_SERVICE) == "midscale"
    # Extended-stay brands span economy → upscale: no honest single bucket.
    assert chain_scale_for_property_type(EXTENDED_STAY) is None
    # Legacy / style values that used to drive the fallback no longer do.
    assert chain_scale_for_property_type("Luxury") is None
    assert chain_scale_for_property_type("Lifestyle") is None
    assert chain_scale_for_property_type(None) is None


def test_deal_bodies_accept_only_taxonomy_values():
    from pydantic import ValidationError

    from app.api.deals import CreateDealBody, UpdateDealBody

    assert CreateDealBody(name="x").service is None
    assert CreateDealBody(name="x", service="full service").service == FULL_SERVICE
    assert UpdateDealBody(service="Select-Service").service == SELECT_SERVICE
    assert UpdateDealBody(service=None).service is None  # null clears
    with pytest.raises(ValidationError):
        CreateDealBody(name="x", service="Luxury")
    with pytest.raises(ValidationError):
        UpdateDealBody(service="Lifestyle")


def test_schema_enum_is_the_taxonomy():
    from fondok_schemas.deal import Service

    assert {s.value for s in Service} == set(PROPERTY_TYPES)
