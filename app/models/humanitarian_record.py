from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


NonEmptyString = Annotated[str, Field(min_length=1)]

CanonicalToken = Annotated[
    str,
    Field(
        min_length=1,
        pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$",
    ),
]


class HCPModel(BaseModel):
    """
    Base model shared by canonical HCP data structures.

    Unknown optional fields are preserved to support compatible protocol
    evolution. Recognized string values are stripped of surrounding
    whitespace before validation.
    """

    model_config = ConfigDict(
        extra="allow",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class Subject(HCPModel):
    """
    Living being referenced by one Humanitarian Observation.

    A Subject describes observable humanitarian evidence. It does not
    represent a permanent identity or personal profile.
    """

    type: Literal["human", "animal"]

    reported_label: NonEmptyString | None = None

    estimated_age: Annotated[int, Field(ge=0)] | None = None

    recognition_features: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_subject_fields(self) -> "Subject":
        """
        Ensure that human-only fields are not used for animal Subjects.
        """
        if self.type == "animal" and self.estimated_age is not None:
            raise ValueError(
                "estimated_age is applicable only to human Subjects"
            )

        return self


class Observation(HCPModel):
    """
    Humanitarian evidence observed at one specific moment in time.

    The Observation describes what happened, who reported it, when it was
    observed and, when available, where it occurred.
    """

    event_type: CanonicalToken

    reported_location: NonEmptyString | None = None

    reported_by: CanonicalToken

    observed_at: datetime

    public_contact: NonEmptyString | None = None

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: datetime) -> datetime:
        """
        Require a timezone-aware timestamp and normalize it to UTC.
        """
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "observed_at must include an ISO 8601 timezone"
            )

        return value.astimezone(timezone.utc)

    @field_serializer("observed_at", when_used="json")
    def serialize_observed_at(self, value: datetime) -> str:
        """
        Serialize the canonical timestamp in UTC using the RFC 3339 Z suffix.
        """
        return value.astimezone(timezone.utc).isoformat().replace(
            "+00:00",
            "Z",
        )


class HumanitarianRecord(HCPModel):
    """
    Canonical representation of one Humanitarian Observation.

    The record identifier identifies this record only. It never identifies
    the human or animal described by the Subject.
    """

    id: UUID

    schema_version: Literal["0.5"] = "0.5"

    source_client: NonEmptyString

    subject: Subject

    observation: Observation

    @field_serializer("id", when_used="json")
    def serialize_id(self, value: UUID) -> str:
        """
        Serialize the record UUID as its canonical lowercase string.
        """
        return str(value)

