python
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_serializer, model_validator

from app.models.humanitarian_record import HCPModel, NonEmptyString


CorrelationScore = Annotated[
    float,
    Field(
        ge=0.0,
        le=100.0,
        description="Correlation score expressed as a percentage.",
    ),
]


SignalContribution = Annotated[
    float,
    Field(
        ge=0.0,
        le=100.0,
        description="Contribution of one signal to the correlation score.",
    ),
]


class CorrelationConfidence(StrEnum):
    """
    Human-readable interpretation of a correlation score.

    Confidence describes the strength of the available evidence. It does not
    confirm that two observations refer to the same human or animal.
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
    Explainable contribution of one humanitarian evidence signal.

    A signal records which field was evaluated, how it compared and how much
    it contributed to the final correlation score.
    """

    field: NonEmptyString

    status: CorrelationSignalStatus

    contribution: SignalContribution = 0.0

    explanation: NonEmptyString

    query_value: str | int | float | bool | None = None

    record_value: str | int | float | bool | None = None

    @model_validator(mode="after")
    def validate_signal_contribution(self) -> "CorrelationSignal":
        """
        Ensure conflicting or unavailable evidence does not contribute
        positively to the correlation score.
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
    Explainable local correlation between an HCP Query and one candidate
    Humanitarian Record.

    The result expresses compatibility between humanitarian observations.
    It must never be interpreted as identity confirmation.
    """

    record_id: UUID

    subject_type: Literal["human", "animal"]

    score: CorrelationScore

    confidence: CorrelationConfidence

    signals: list[CorrelationSignal] = Field(default_factory=list)

    @field_serializer("record_id", when_used="json")
    def serialize_record_id(self, value: UUID) -> str:
        """
        Serialize the candidate record identifier as a lowercase UUID string.
        """
        return str(value)

    @model_validator(mode="after")
    def validate_confidence_for_score(self) -> "CorrelationResult":
        """
        Ensure the confidence label corresponds to the numerical score.

        Score bands are presentation categories used by this reference
        implementation. They do not represent identity certainty.
        """
        expected_confidence = confidence_from_score(self.score)

        if self.confidence != expected_confidence:
            raise ValueError(
                "confidence does not correspond to the correlation score; "
                f"expected '{expected_confidence.value}'"
            )

        return self


def confidence_from_score(score: float) -> CorrelationConfidence:
    """
    Convert a correlation percentage into a human-readable confidence band.

    These bands make results easier to present consistently while preserving
    the original numerical score.
    """
    if score >= 85.0:
        return CorrelationConfidence.VERY_HIGH

    if score >= 70.0:
        return CorrelationConfidence.HIGH

    if score >= 50.0:
        return CorrelationConfidence.MODERATE

    if score >= 30.0:
        return CorrelationConfidence.LOW

    return CorrelationConfidence.VERY_LOW

