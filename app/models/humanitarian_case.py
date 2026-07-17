from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import (
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.models.humanitarian_record import HCPModel, NonEmptyString


EVENT_TYPE_PATTERN = r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"


def utc_now() -> datetime:
    """
    Return the current time as a timezone-aware UTC datetime.
    """
    return datetime.now(timezone.utc)


def generate_case_id() -> str:
    """
    Generate a local Humanitarian Case identifier.

    A Humanitarian Case identifier identifies only a local interpretation.
    It must never be treated as a Subject identifier.
    """
    return f"case-{uuid4()}"


class CurrentSituation(HCPModel):
    """
    Current local humanitarian interpretation derived from related records.

    This object is optional and must not be presented as verified fact.
    """

    likely_event_type: str | None = Field(
        default=None,
        min_length=1,
        pattern=EVENT_TYPE_PATTERN,
    )

    reported_location: NonEmptyString | None = None

    observed_at: datetime | None = None

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        """
        Require a timezone-aware timestamp and normalize it to UTC.
        """
        if value is None:
            return None

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "current_situation.observed_at must include an ISO 8601 "
                "timezone"
            )

        return value.astimezone(timezone.utc)

    @field_serializer("observed_at", when_used="json")
    def serialize_observed_at(
        self,
        value: datetime | None,
    ) -> str | None:
        """
        Serialize the timestamp in UTC using the RFC 3339 Z suffix.
        """
        if value is None:
            return None

        return value.astimezone(timezone.utc).isoformat().replace(
            "+00:00",
            "Z",
        )

    @model_validator(mode="after")
    def require_at_least_one_property(self) -> "CurrentSituation":
        """
        Require at least one current-situation property.
        """
        if (
            self.likely_event_type is None
            and self.reported_location is None
            and self.observed_at is None
        ):
            raise ValueError(
                "current_situation must contain at least one property"
            )

        return self


class EvidenceItem(HCPModel):
    """
    Evidence that supports or conflicts with a local humanitarian
    interpretation.
    """

    type: str = Field(
        min_length=1,
        pattern=EVENT_TYPE_PATTERN,
    )

    description: NonEmptyString

    related_record_ids: list[UUID] = Field(default_factory=list)

    @field_validator("related_record_ids")
    @classmethod
    def validate_unique_record_ids(
        cls,
        value: list[UUID],
    ) -> list[UUID]:
        """
        Reject duplicate Humanitarian Record identifiers.
        """
        if len(value) != len(set(value)):
            raise ValueError(
                "evidence related_record_ids must not contain duplicates"
            )

        return value

    @field_serializer("related_record_ids", when_used="json")
    def serialize_related_record_ids(
        self,
        value: list[UUID],
    ) -> list[str]:
        """
        Serialize record identifiers as lowercase UUID strings.
        """
        return [str(record_id) for record_id in value]


class CaseCorrelation(HCPModel):
    """
    Implementation-specific correlation interpretation.

    HCP does not prescribe a numerical scale or scoring methodology.
    """

    score: float

    evidence_level: str | None = Field(
        default=None,
        min_length=1,
        pattern=EVENT_TYPE_PATTERN,
    )

    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)

    conflicting_evidence: list[EvidenceItem] = Field(default_factory=list)

    reasoning: NonEmptyString | None = None


class RelatedRecord(HCPModel):
    """
    Reference to an immutable Humanitarian Record contributing evidence.
    """

    record_id: UUID

    event_type: str = Field(
        min_length=1,
        pattern=EVENT_TYPE_PATTERN,
    )

    observed_at: datetime

    source: NonEmptyString | None = None

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        """
        Require a timezone-aware timestamp and normalize it to UTC.
        """
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "related_record.observed_at must include an ISO 8601 timezone"
            )

        return value.astimezone(timezone.utc)

    @field_serializer("record_id", when_used="json")
    def serialize_record_id(self, value: UUID) -> str:
        """
        Serialize the record identifier as a lowercase UUID string.
        """
        return str(value)

    @field_serializer("observed_at", when_used="json")
    def serialize_observed_at(self, value: datetime) -> str:
        """
        Serialize the timestamp in UTC using the RFC 3339 Z suffix.
        """
        return value.astimezone(timezone.utc).isoformat().replace(
            "+00:00",
            "Z",
        )


class TimelineEntry(HCPModel):
    """
    Local chronological presentation of a related humanitarian observation.

    A timeline is an interpretation aid and does not establish verified
    personal history.
    """

    record_id: UUID

    event_type: str = Field(
        min_length=1,
        pattern=EVENT_TYPE_PATTERN,
    )

    observed_at: datetime

    reported_location: NonEmptyString | None = None

    description: NonEmptyString | None = None

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        """
        Require a timezone-aware timestamp and normalize it to UTC.
        """
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "timeline_entry.observed_at must include an ISO 8601 timezone"
            )

        return value.astimezone(timezone.utc)

    @field_serializer("record_id", when_used="json")
    def serialize_record_id(self, value: UUID) -> str:
        """
        Serialize the record identifier as a lowercase UUID string.
        """
        return str(value)

    @field_serializer("observed_at", when_used="json")
    def serialize_observed_at(self, value: datetime) -> str:
        """
        Serialize the timestamp in UTC using the RFC 3339 Z suffix.
        """
        return value.astimezone(timezone.utc).isoformat().replace(
            "+00:00",
            "Z",
        )


class CaseVerification(HCPModel):
    """
    Local verification status of a Humanitarian Case.
    """

    status: Literal[
        "unverified",
        "under_review",
        "human_verified",
        "rejected",
    ]

    message: NonEmptyString

    verified_at: datetime | None = None

    verified_by: NonEmptyString | None = None

    @field_validator("verified_at")
    @classmethod
    def validate_verified_at(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        """
        Require a timezone-aware verification timestamp and normalize it
        to UTC.
        """
        if value is None:
            return None

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "verification.verified_at must include an ISO 8601 timezone"
            )

        return value.astimezone(timezone.utc)

    @field_serializer("verified_at", when_used="json")
    def serialize_verified_at(
        self,
        value: datetime | None,
    ) -> str | None:
        """
        Serialize the timestamp in UTC using the RFC 3339 Z suffix.
        """
        if value is None:
            return None

        return value.astimezone(timezone.utc).isoformat().replace(
            "+00:00",
            "Z",
        )

    @model_validator(mode="after")
    def validate_human_verification(self) -> "CaseVerification":
        """
        Require verified_at whenever the case is human verified.
        """
        if self.status == "human_verified" and self.verified_at is None:
            raise ValueError(
                "verified_at is required when verification status is "
                "human_verified"
            )

        return self


class HumanitarianCase(HCPModel):
    """
    Local humanitarian interpretation generated from correlated HCP records.

    A Humanitarian Case:

    - is not a Humanitarian Record;
    - is not canonical protocol evidence;
    - does not establish identity;
    - must not be synchronized between HCP Nodes;
    - may change when new Humanitarian Records become available.
    """

    case_id: NonEmptyString = Field(default_factory=generate_case_id)

    generated_at: datetime = Field(default_factory=utc_now)

    source_query_id: NonEmptyString | None = None

    humanitarian_summary: NonEmptyString

    current_situation: CurrentSituation | None = None

    correlation: CaseCorrelation

    related_records: list[RelatedRecord] = Field(min_length=1)

    humanitarian_timeline: list[TimelineEntry] = Field(
        default_factory=list
    )

    verification: CaseVerification

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: datetime) -> datetime:
        """
        Require a timezone-aware generation timestamp and normalize it to UTC.
        """
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "generated_at must include an ISO 8601 timezone"
            )

        return value.astimezone(timezone.utc)

    @field_serializer("generated_at", when_used="json")
    def serialize_generated_at(self, value: datetime) -> str:
        """
        Serialize the timestamp in UTC using the RFC 3339 Z suffix.
        """
        return value.astimezone(timezone.utc).isoformat().replace(
            "+00:00",
            "Z",
        )

    @model_validator(mode="after")
    def validate_case_consistency(self) -> "HumanitarianCase":
        """
        Validate references and chronological consistency inside the case.
        """
        related_ids = [
            related_record.record_id
            for related_record in self.related_records
        ]

        if len(related_ids) != len(set(related_ids)):
            raise ValueError(
                "related_records must not contain duplicate record identifiers"
            )

        related_id_set = set(related_ids)

        for evidence in (
            self.correlation.supporting_evidence
            + self.correlation.conflicting_evidence
        ):
            unknown_ids = set(evidence.related_record_ids) - related_id_set

            if unknown_ids:
                raise ValueError(
                    "evidence must reference only identifiers contained in "
                    "related_records"
                )

        timeline_ids = [
            entry.record_id
            for entry in self.humanitarian_timeline
        ]

        if len(timeline_ids) != len(set(timeline_ids)):
            raise ValueError(
                "humanitarian_timeline must not contain duplicate record "
                "identifiers"
            )

        unknown_timeline_ids = set(timeline_ids) - related_id_set

        if unknown_timeline_ids:
            raise ValueError(
                "humanitarian_timeline must reference only identifiers "
                "contained in related_records"
            )

        timeline_timestamps = [
            entry.observed_at
            for entry in self.humanitarian_timeline
        ]

        if timeline_timestamps != sorted(timeline_timestamps):
            raise ValueError(
                "humanitarian_timeline must be ordered chronologically"
            )

        return self

    @property
    def record_ids(self) -> list[UUID]:
        """
        Return the identifiers of all related Humanitarian Records.
        """
        return [
            related_record.record_id
            for related_record in self.related_records
        ]
