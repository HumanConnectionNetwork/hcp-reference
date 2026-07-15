python
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, field_serializer, field_validator, model_validator

from app.models.correlation import CorrelationResult
from app.models.humanitarian_record import HCPModel, NonEmptyString
from app.models.query import HumanitarianQuery


def utc_now() -> datetime:
    """
    Return the current time as a timezone-aware UTC datetime.
    """
    return datetime.now(timezone.utc)


class HumanitarianCase(HCPModel):
    """
    Local humanitarian interpretation generated from correlation results.

    A Humanitarian Case groups Humanitarian Records that may describe the
    same humanitarian situation.

    It is a derived implementation object:

    - it is not a Humanitarian Record;
    - it is not canonical protocol evidence;
    - it does not establish identity;
    - it must not be synchronized between HCP Nodes;
    - it may change whenever new records become available.
    """

    id: UUID = Field(default_factory=uuid4)

    generated_at: datetime = Field(default_factory=utc_now)

    subject_type: Literal["human", "animal"]

    query: HumanitarianQuery

    results: list[CorrelationResult] = Field(
        min_length=1,
        description=(
            "Correlation results included in this local humanitarian case."
        ),
    )

    reasoning: NonEmptyString

    local_only: Literal[True] = True

    human_verification_required: Literal[True] = True

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

    @field_serializer("id", when_used="json")
    def serialize_id(self, value: UUID) -> str:
        """
        Serialize the local case identifier as a lowercase UUID string.
        """
        return str(value)

    @field_serializer("generated_at", when_used="json")
    def serialize_generated_at(self, value: datetime) -> str:
        """
        Serialize the generation timestamp in UTC with the RFC 3339 Z suffix.
        """
        return value.astimezone(timezone.utc).isoformat().replace(
            "+00:00",
            "Z",
        )

    @model_validator(mode="after")
    def validate_case_consistency(self) -> "HumanitarianCase":
        """
        Ensure that the Query and all correlation results refer to the same
        Subject type and that no candidate record appears more than once.
        """
        if self.query.subject.type != self.subject_type:
            raise ValueError(
                "query Subject type must match Humanitarian Case subject_type"
            )

        record_ids: list[UUID] = []

        for result in self.results:
            if result.subject_type != self.subject_type:
                raise ValueError(
                    "all correlation results must match the "
                    "Humanitarian Case subject_type"
                )

            record_ids.append(result.record_id)

        if len(record_ids) != len(set(record_ids)):
            raise ValueError(
                "Humanitarian Case results must not contain duplicate "
                "record identifiers"
            )

        return self

    @property
    def record_ids(self) -> list[UUID]:
        """
        Return the Humanitarian Record identifiers represented by the case.

        The identifiers are derived from the correlation results and are not
        stored independently.
        """
        return [result.record_id for result in self.results]
