from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    Field,
    field_serializer,
    model_validator,
)

from app.models.humanitarian_record import (
    HCPModel,
    NonEmptyString,
)


CorrelationScore = Annotated[
    float,
    Field(
        ge=0.0,
        le=100.0,
        description=(
            "Descriptive, spatial and temporal compatibility expressed "
            "as a percentage."
        ),
    ),
]


SignalContribution = Annotated[
    float,
    Field(
        ge=0.0,
        le=100.0,
        description=(
            "Contribution of one supporting signal to the compatibility "
            "score."
        ),
    ),
]


class CorrelationConfidence(StrEnum):
    """
    Human-readable strength of the evidence available for a correlation.

    Confidence is independent from the compatibility score.

    Examples:

    - compatibility may be very high because a name matches exactly,
      while evidence strength remains low because no age, location or
      descriptive features are available;

    - compatibility may be moderate while evidence strength is high because
      many independent fields were compared and some of them conflict.

    Confidence never confirms identity.
    """

    VERY_LOW = "very_low"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


class CorrelationSignalStatus(StrEnum):
    """
    Relationship between one Query field and one Record field.
    """

    MATCH = "match"
    PARTIAL_MATCH = "partial_match"
    CONFLICT = "conflict"
    NOT_AVAILABLE = "not_available"


class CorrelationSignal(HCPModel):
    """
    Explainable comparison of one humanitarian evidence signal.

    A signal records:

    - which field was evaluated;
    - whether it supports or conflicts with the candidate;
    - how much it contributes to compatibility;
    - the values that were compared.

    Signals describe compatibility between reports. They do not establish
    identity.
    """

    field: NonEmptyString

    status: CorrelationSignalStatus

    contribution: SignalContribution = 0.0

    explanation: NonEmptyString

    query_value: (
        str
        | int
        | float
        | bool
        | None
    ) = None

    record_value: (
        str
        | int
        | float
        | bool
        | None
    ) = None

    @model_validator(mode="after")
    def validate_signal_contribution(
        self,
    ) -> "CorrelationSignal":
        """
        Ensure only supporting signals contribute positively.

        Conflicting and unavailable evidence remains visible for human
        interpretation, but cannot increase the compatibility score.
        """
        if (
            self.status
            in {
                CorrelationSignalStatus.CONFLICT,
                CorrelationSignalStatus.NOT_AVAILABLE,
            }
            and self.contribution != 0.0
        ):
            raise ValueError(
                "conflicting or unavailable signals must have a "
                "contribution of 0"
            )

        return self


class CorrelationResult(HCPModel):
    """
    Explainable local correlation between one HCP Query and one candidate
    Humanitarian Record.

    score:
        Compatibility between the evidence values that could be compared.

    confidence:
        Strength and breadth of the available evidence.

    These values are intentionally independent.

    A result with 100% compatibility may still have low evidence strength
    when only one weak or common field was available.

    A result with moderate compatibility may have high evidence strength
    when several independent signals were evaluated.

    Neither score nor confidence confirms identity.
    """

    record_id: UUID

    subject_type: Literal[
        "human",
        "animal",
    ]

    score: CorrelationScore

    confidence: CorrelationConfidence

    signals: list[
        CorrelationSignal
    ] = Field(
        default_factory=list
    )

    @field_serializer(
        "record_id",
        when_used="json",
    )
    def serialize_record_id(
        self,
        value: UUID,
    ) -> str:
        """
        Serialize the candidate record identifier as a canonical UUID string.
        """
        return str(value)

    @model_validator(mode="after")
    def validate_result_consistency(
        self,
    ) -> "CorrelationResult":
        """
        Validate only structural consistency.

        Compatibility score and evidence strength are not required to belong
        to the same numerical band.
        """
        supporting_signals = [
            signal
            for signal in self.signals
            if signal.status
            in {
                CorrelationSignalStatus.MATCH,
                CorrelationSignalStatus.PARTIAL_MATCH,
            }
        ]

        if (
            self.score > 0.0
            and not supporting_signals
        ):
            raise ValueError(
                "a positive correlation score requires at least one "
                "supporting signal"
            )

        return self


def confidence_from_score(
    score: float,
) -> CorrelationConfidence:
    """
    Convert an evidence-strength percentage into a readable evidence band.

    Despite the historical function name, callers may pass an independently
    calculated evidence-strength value rather than the compatibility score.

    The function remains available under its current name to preserve
    compatibility with existing services.
    """
    if score >= 85.0:
        return (
            CorrelationConfidence
            .VERY_HIGH
        )

    if score >= 70.0:
        return (
            CorrelationConfidence
            .HIGH
        )

    if score >= 50.0:
        return (
            CorrelationConfidence
            .MODERATE
        )

    if score >= 30.0:
        return (
            CorrelationConfidence
            .LOW
        )

    return (
        CorrelationConfidence
        .VERY_LOW
    )
