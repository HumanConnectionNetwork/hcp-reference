from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import (
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.models.humanitarian_record import (
    DeclaredLocation,
    HCPModel,
    NonEmptyString,
)


EVENT_TYPE_PATTERN = (
    r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"
)


def utc_now() -> datetime:
    """
    Return the current time as a timezone-aware UTC datetime.
    """
    return datetime.now(
        timezone.utc
    )


def generate_case_id() -> str:
    """
    Generate a local Humanitarian Case identifier.

    A Humanitarian Case identifier identifies only a local interpretation.
    It must never be treated as a Subject identifier.
    """
    return f"case-{uuid4()}"


class CurrentSituation(HCPModel):
    """
    Most recent local humanitarian interpretation derived from related
    Humanitarian Records.

    The current situation describes the latest report included in the
    probable case history.

    It is not:

    - verified identity information;
    - proof that the Subject remains in the declared location;
    - a replacement for the original Humanitarian Record.

    Schema transition:

    - declared_location carries structured geographic context;
    - reported_location remains temporarily available for schema 0.5
      compatibility and presentation.
    """

    likely_event_type: str | None = Field(
        default=None,
        min_length=1,
        pattern=EVENT_TYPE_PATTERN,
    )

    declared_location: DeclaredLocation | None = None

    reported_location: NonEmptyString | None = Field(
        default=None,
        description=(
            "Legacy or presentation-oriented free-text location."
        ),
    )

    observed_at: datetime | None = None

    source_record_id: UUID | None = None

    @field_validator(
        "observed_at",
    )
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

        if (
            value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(
                "current_situation.observed_at must include an ISO 8601 "
                "timezone"
            )

        return value.astimezone(
            timezone.utc
        )

    @field_serializer(
        "observed_at",
        when_used="json",
    )
    def serialize_observed_at(
        self,
        value: datetime | None,
    ) -> str | None:
        """
        Serialize the timestamp in UTC using the RFC 3339 Z suffix.
        """
        if value is None:
            return None

        return (
            value
            .astimezone(timezone.utc)
            .isoformat()
            .replace(
                "+00:00",
                "Z",
            )
        )

    @field_serializer(
        "source_record_id",
        when_used="json",
    )
    def serialize_source_record_id(
        self,
        value: UUID | None,
    ) -> str | None:
        """
        Serialize the source Humanitarian Record identifier.
        """
        if value is None:
            return None

        return str(value)

    @model_validator(mode="after")
    def require_at_least_one_property(
        self,
    ) -> "CurrentSituation":
        """
        Require at least one useful current-situation property.
        """
        if (
            self.likely_event_type is None
            and self.declared_location is None
            and self.reported_location is None
            and self.observed_at is None
            and self.source_record_id is None
        ):
            raise ValueError(
                "current_situation must contain at least one property"
            )

        return self

    def location_display_text(
        self,
    ) -> str | None:
        """
        Return a readable geographic label.

        Structured location is preferred.
        """
        if self.declared_location is not None:
            return (
                self.declared_location
                .to_display_text()
            )

        return self.reported_location


class EvidenceItem(HCPModel):
    """
    Evidence that supports or conflicts with a local humanitarian
    interpretation.

    Evidence always remains traceable to one or more immutable
    Humanitarian Records.
    """

    type: str = Field(
        min_length=1,
        pattern=EVENT_TYPE_PATTERN,
    )

    description: NonEmptyString

    related_record_ids: list[UUID] = Field(
        default_factory=list
    )

    @field_validator(
        "related_record_ids",
    )
    @classmethod
    def validate_unique_record_ids(
        cls,
        value: list[UUID],
    ) -> list[UUID]:
        """
        Reject duplicate Humanitarian Record identifiers.
        """
        if (
            len(value)
            != len(set(value))
        ):
            raise ValueError(
                "evidence related_record_ids must not contain duplicates"
            )

        return value

    @field_serializer(
        "related_record_ids",
        when_used="json",
    )
    def serialize_related_record_ids(
        self,
        value: list[UUID],
    ) -> list[str]:
        """
        Serialize record identifiers as canonical UUID strings.
        """
        return [
            str(record_id)
            for record_id in value
        ]


class CaseCorrelation(HCPModel):
    """
    Implementation-specific correlation interpretation.

    The score represents compatibility among the evidence that was actually
    compared.

    evidence_level represents the amount and strength of independent
    supporting information.

    Neither value establishes identity.
    """

    score: float = Field(
        ge=0.0,
        le=100.0,
    )

    evidence_level: str | None = Field(
        default=None,
        min_length=1,
        pattern=EVENT_TYPE_PATTERN,
    )

    supporting_evidence: list[
        EvidenceItem
    ] = Field(
        default_factory=list
    )

    conflicting_evidence: list[
        EvidenceItem
    ] = Field(
        default_factory=list
    )

    reasoning: NonEmptyString | None = None


class RelatedRecord(HCPModel):
    """
    Reference to one immutable Humanitarian Record contributing to a
    probable case history.

    This compact representation contains the information needed to show a
    useful report card without presenting a bare UUID as the primary result.

    The full report remains accessible through record_id.
    """

    record_id: UUID

    event_type: str = Field(
        min_length=1,
        pattern=EVENT_TYPE_PATTERN,
    )

    observed_at: datetime

    declared_location: DeclaredLocation | None = None

    reported_location: NonEmptyString | None = Field(
        default=None,
        description=(
            "Legacy or presentation-oriented free-text location."
        ),
    )

    source: NonEmptyString | None = None

    public_contact_available: bool = False

    @field_validator(
        "observed_at",
    )
    @classmethod
    def validate_observed_at(
        cls,
        value: datetime,
    ) -> datetime:
        """
        Require a timezone-aware timestamp and normalize it to UTC.
        """
        if (
            value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(
                "related_record.observed_at must include an ISO 8601 timezone"
            )

        return value.astimezone(
            timezone.utc
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
        Serialize the record identifier canonically.
        """
        return str(value)

    @field_serializer(
        "observed_at",
        when_used="json",
    )
    def serialize_observed_at(
        self,
        value: datetime,
    ) -> str:
        """
        Serialize the timestamp in UTC using the RFC 3339 Z suffix.
        """
        return (
            value
            .astimezone(timezone.utc)
            .isoformat()
            .replace(
                "+00:00",
                "Z",
            )
        )

    def location_display_text(
        self,
    ) -> str | None:
        """
        Return a readable location for public presentation.
        """
        if self.declared_location is not None:
            return (
                self.declared_location
                .to_display_text()
            )

        return self.reported_location


class TimelineEntry(HCPModel):
    """
    Chronological presentation of one report in a probable case history.

    A timeline entry helps people understand:

    - what was reported;
    - where it was reported;
    - when it was reported;
    - which source record can be opened.

    It does not establish verified personal history.
    """

    record_id: UUID

    event_type: str = Field(
        min_length=1,
        pattern=EVENT_TYPE_PATTERN,
    )

    observed_at: datetime

    declared_location: DeclaredLocation | None = None

    reported_location: NonEmptyString | None = Field(
        default=None,
        description=(
            "Legacy or presentation-oriented free-text location."
        ),
    )

    description: NonEmptyString | None = None

    public_contact_available: bool = False

    @field_validator(
        "observed_at",
    )
    @classmethod
    def validate_observed_at(
        cls,
        value: datetime,
    ) -> datetime:
        """
        Require a timezone-aware timestamp and normalize it to UTC.
        """
        if (
            value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(
                "timeline_entry.observed_at must include an ISO 8601 timezone"
            )

        return value.astimezone(
            timezone.utc
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
        Serialize the record identifier canonically.
        """
        return str(value)

    @field_serializer(
        "observed_at",
        when_used="json",
    )
    def serialize_observed_at(
        self,
        value: datetime,
    ) -> str:
        """
        Serialize the timestamp in UTC using the RFC 3339 Z suffix.
        """
        return (
            value
            .astimezone(timezone.utc)
            .isoformat()
            .replace(
                "+00:00",
                "Z",
            )
        )

    def location_display_text(
        self,
    ) -> str | None:
        """
        Return the structured or legacy location as readable text.
        """
        if self.declared_location is not None:
            return (
                self.declared_location
                .to_display_text()
            )

        return self.reported_location


class CaseVerification(HCPModel):
    """
    Local verification status of a Humanitarian Case.

    Verification belongs to the local implementation. It does not transform
    the case into canonical identity evidence.
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

    @field_validator(
        "verified_at",
    )
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

        if (
            value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(
                "verification.verified_at must include an ISO 8601 timezone"
            )

        return value.astimezone(
            timezone.utc
        )

    @field_serializer(
        "verified_at",
        when_used="json",
    )
    def serialize_verified_at(
        self,
        value: datetime | None,
    ) -> str | None:
        """
        Serialize the timestamp in UTC using the RFC 3339 Z suffix.
        """
        if value is None:
            return None

        return (
            value
            .astimezone(timezone.utc)
            .isoformat()
            .replace(
                "+00:00",
                "Z",
            )
        )

    @model_validator(mode="after")
    def validate_human_verification(
        self,
    ) -> "CaseVerification":
        """
        Require verified_at whenever the case is human verified.
        """
        if (
            self.status == "human_verified"
            and self.verified_at is None
        ):
            raise ValueError(
                "verified_at is required when verification status is "
                "human_verified"
            )

        return self


class HumanitarianCase(HCPModel):
    """
    Local humanitarian interpretation generated from correlated HCP reports.

    Public clients may present this object as:

    - Caso relacionado;
    - Historia del caso;
    - Reportes del caso.

    Internally it remains a HumanitarianCase.

    A Humanitarian Case:

    - is not a Humanitarian Record;
    - is not canonical identity evidence;
    - does not confirm that reports describe the same Subject;
    - must not replace the original records;
    - may change when new reports become available;
    - must not be synchronized as if it were canonical evidence.
    """

    case_id: NonEmptyString = Field(
        default_factory=generate_case_id
    )

    generated_at: datetime = Field(
        default_factory=utc_now
    )

    source_query_id: NonEmptyString | None = None

    humanitarian_summary: NonEmptyString

    current_situation: CurrentSituation | None = None

    correlation: CaseCorrelation

    related_records: list[
        RelatedRecord
    ] = Field(
        min_length=1
    )

    humanitarian_timeline: list[
        TimelineEntry
    ] = Field(
        default_factory=list
    )

    verification: CaseVerification

    @field_validator(
        "generated_at",
    )
    @classmethod
    def validate_generated_at(
        cls,
        value: datetime,
    ) -> datetime:
        """
        Require a timezone-aware generation timestamp and normalize it to UTC.
        """
        if (
            value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(
                "generated_at must include an ISO 8601 timezone"
            )

        return value.astimezone(
            timezone.utc
        )

    @field_serializer(
        "generated_at",
        when_used="json",
    )
    def serialize_generated_at(
        self,
        value: datetime,
    ) -> str:
        """
        Serialize the timestamp in UTC using the RFC 3339 Z suffix.
        """
        return (
            value
            .astimezone(timezone.utc)
            .isoformat()
            .replace(
                "+00:00",
                "Z",
            )
        )

    @model_validator(mode="after")
    def validate_case_consistency(
        self,
    ) -> "HumanitarianCase":
        """
        Validate references and chronology inside the case.
        """
        related_ids = [
            related_record.record_id
            for related_record
            in self.related_records
        ]

        if (
            len(related_ids)
            != len(set(related_ids))
        ):
            raise ValueError(
                "related_records must not contain duplicate record identifiers"
            )

        related_id_set = set(
            related_ids
        )

        for evidence in (
            self.correlation.supporting_evidence
            + self.correlation.conflicting_evidence
        ):
            unknown_ids = (
                set(
                    evidence.related_record_ids
                )
                - related_id_set
            )

            if unknown_ids:
                raise ValueError(
                    "evidence must reference only identifiers contained in "
                    "related_records"
                )

        timeline_ids = [
            entry.record_id
            for entry
            in self.humanitarian_timeline
        ]

        if (
            len(timeline_ids)
            != len(set(timeline_ids))
        ):
            raise ValueError(
                "humanitarian_timeline must not contain duplicate record "
                "identifiers"
            )

        unknown_timeline_ids = (
            set(timeline_ids)
            - related_id_set
        )

        if unknown_timeline_ids:
            raise ValueError(
                "humanitarian_timeline must reference only identifiers "
                "contained in related_records"
            )

        timeline_timestamps = [
            entry.observed_at
            for entry
            in self.humanitarian_timeline
        ]

        if (
            timeline_timestamps
            != sorted(
                timeline_timestamps
            )
        ):
            raise ValueError(
                "humanitarian_timeline must be ordered chronologically"
            )

        if (
            self.current_situation is not None
            and self.current_situation.source_record_id is not None
            and self.current_situation.source_record_id
            not in related_id_set
        ):
            raise ValueError(
                "current_situation.source_record_id must reference a record "
                "contained in related_records"
            )

        return self

    @property
    def record_ids(
        self,
    ) -> list[UUID]:
        """
        Return identifiers of all reports included in the case.
        """
        return [
            related_record.record_id
            for related_record
            in self.related_records
        ]
