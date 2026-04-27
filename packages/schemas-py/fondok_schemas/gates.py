"""HITL gates — analyst approval points between AI stages."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GateDecision(BaseModel):
    """Base HITL decision — shared fields for every gate."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    tenant_id: Annotated[str, Field(min_length=1)]
    decided_by: UUID
    decided_at: datetime
    approved: bool
    decision: Literal["approve", "reject", "request_changes"]
    comment: Annotated[str, Field(max_length=4000)] | None = None


class Gate1Decision(GateDecision):
    """Gate 1: Extraction Verification.

    Analyst has reviewed every extracted field and confirms it matches the
    source document. They may edit fields and request re-extraction.
    """

    edits: dict[str, str] = Field(
        default_factory=dict,
        description="field_path -> corrected value (string-encoded).",
    )
    reextract_documents: list[UUID] = Field(default_factory=list)


class Gate2Decision(GateDecision):
    """Gate 2: Underwriting Sign-off.

    Analyst has reviewed engine outputs, variance flags, and the draft IC
    memo. They may edit memo sections and grant waivers on critical flags.
    """

    recommendation: Literal[
        "Proceed_to_LOI",
        "Proceed_with_Conditions",
        "Pass",
        "Refer_Up",
    ]
    edits: dict[str, str] = Field(
        default_factory=dict,
        description="memo_section_id -> revised markdown.",
    )
    waivers_granted: list[str] = Field(
        default_factory=list,
        description="variance flag IDs being waived.",
    )
    waiver_justification: Annotated[str, Field(max_length=2000)] | None = None

    @model_validator(mode="after")
    def _waiver_justification_required(self) -> "Gate2Decision":
        if self.waivers_granted and (
            not self.waiver_justification
            or len(self.waiver_justification.strip()) < 50
        ):
            raise ValueError(
                "waiver_justification must be at least 50 characters "
                "when waivers_granted is non-empty"
            )
        return self
