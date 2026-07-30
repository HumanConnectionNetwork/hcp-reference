from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import (
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.models.humanitarian_record import (
    CanonicalToken,
    DeclaredLocation,
    HCPModel,
    NonEmptyString,
)


class SubjectQuery(HCPModel):
    """
    Descriptive information known about the human or animal being searched.

    A Subject Query does not represent or verify identity. It contains only
    the information voluntarily supplied by the person performing the search.

    Descriptive correlation may use:

    - name or reported label;
    - estimated age for humans;
    - recognition features;
    - species, breed and size for animals.
    """

    type: Literal[
        "human",
        "animal",
    ]

    reported_label: NonEmptyString | None = None

    estimated_age: int | None = Field(
        default=None,
        ge=0,
        le=130,
    )

    recognition_features: NonEmptyString | None = None

    species: NonEmptyString | None = None

    breed: NonEmptyString | None = None

    size: CanonicalToken | None = None

    @model_validator(mode="after")
    def validate_subject_query(
        self,
    ) -> "SubjectQuery":
        """
        Validate fields according to the selected Subject type.
        """
        if self.type == "animal":
            if self.estimated_age is not None:
                raise ValueError(
                    "estimated_age is applicable only to human "
                    "Subject Queries"
                )

            return self

        animal_fields = {
            "species": self.species,
            "breed": self.breed,
            "size": self.size,
        }

        supplied_animal_fields = [
            field_name
            for field_name, value
            in animal_fields.items()
            if value is not None
        ]

        if supplied_animal_fields:
            joined_fields = ", ".join(
                supplied_animal_fields
            )

            raise ValueError(
                "animal-specific fields are not applicable to a human "
                f"Subject Query: {joined_fields}"
            )

        return self

    def has_descriptive_evidence(
        self,
    ) -> bool:
        """
        Return whether the Query contains useful descriptive information.
        """
        values = [
            self.reported_label,
            self.estimated_age,
            self.recognition_features,
            self.species,
            self.breed,
            self.size,
        ]

        return any(
            value is not None
            for value in values
        )


class ObservationQuery(HCPModel):
    """
    Space-time context voluntarily declared by the person searching.

    This context does not represent the physical location of the device.

    It describes the location considered relevant to the search, such as:

    - the last place where the person or animal was known to be;
    - the place where the disappearance occurred;
    - the place where the subject was recently seen;
    - the area where the person performing the search expects related
      reports to exist.

    Schema transition:

    - declared_location is the structured geographic model for new clients;
    - reported_location remains available for legacy schema 0.5 clients;
    - event_type remains temporarily accepted for compatibility, but must not
      be treated as identity evidence or as an absolute candidate filter.
    """

    declared_location: DeclaredLocation | None = None

    reported_location: NonEmptyString | None = Field(
        default=None,
        description=(
            "Legacy free-text location used by existing schema 0.5 clients."
        ),
    )

    event_type: CanonicalToken | None = Field(
        default=None,
        description=(
            "Legacy optional event criterion. Event type describes a "
            "situation and must not be used as identity evidence."
        ),
    )

    searched_at: datetime = Field(
        default_factory=lambda: datetime.now(
            timezone.utc
        ),
        description=(
            "Timezone-aware moment when the search context was declared."
        ),
    )

    @field_validator("searched_at")
    @classmethod
    def validate_searched_at(
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
                "searched_at must include an ISO 8601 timezone"
            )

        return value.astimezone(
            timezone.utc
        )

    @field_serializer(
        "searched_at",
        when_used="json",
    )
    def serialize_searched_at(
        self,
        value: datetime,
    ) -> str:
        """
        Serialize the search timestamp using the RFC 3339 UTC Z suffix.
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
    def require_search_context(
        self,
    ) -> "ObservationQuery":
        """
        Reject a completely empty explicitly supplied context.

        searched_at is generated automatically and does not count by itself
        as useful search evidence.
        """
        known_context = (
            self.declared_location is not None
            or self.reported_location is not None
            or self.event_type is not None
        )

        extension_context = bool(
            self.model_extra
        )

        if (
            not known_context
            and not extension_context
        ):
            raise ValueError(
                "observation must contain declared_location, a legacy "
                "location or another compatible search-context field"
            )

        return self

    def location_display_text(
        self,
    ) -> str | None:
        """
        Return a readable location while supporting both location formats.
        """
        if self.declared_location is not None:
            return (
                self.declared_location
                .to_display_text()
            )

        return self.reported_location


class HumanitarianQuery(HCPModel):
    """
    Canonical HCP Query for finding related humanitarian reports.

    A Humanitarian Query represents:

    - the descriptive information known by the person searching;
    - the voluntarily declared geographic context;
    - the moment when the search was performed.

    It requests probable continuity between reports. It does not request
    identity confirmation or exact personal matching.

    Correlation principles:

    - space and time determine physical plausibility;
    - name, age and recognition features determine descriptive compatibility;
    - event type describes reported situations and is not identity evidence;
    - the reporting source does not participate in compatibility scoring.
    """

    query_id: UUID | None = None

    subject: SubjectQuery

    observation: ObservationQuery | None = None

    @model_validator(mode="after")
    def require_search_evidence(
        self,
    ) -> "HumanitarianQuery":
        """
        Require at least one useful descriptive or spatial search criterion.

        This prevents queries containing only the Subject type.
        """
        has_descriptive_evidence = (
            self.subject
            .has_descriptive_evidence()
        )

        has_spatial_context = (
            self.observation is not None
            and (
                self.observation.declared_location
                is not None
                or self.observation.reported_location
                is not None
            )
        )

        has_extension_evidence = bool(
            self.model_extra
        )

        if (
            not has_descriptive_evidence
            and not has_spatial_context
            and not has_extension_evidence
        ):
            raise ValueError(
                "a Humanitarian Query must include descriptive evidence "
                "or declared geographic context"
            )

        return self

    @field_serializer(
        "query_id",
        when_used="json",
    )
    def serialize_query_id(
        self,
        value: UUID | None,
    ) -> str | None:
        """
        Serialize the optional Query UUID canonically.
        """
        if value is None:
            return None

        return str(value)

    def searched_at(
        self,
    ) -> datetime | None:
        """
        Return the moment associated with the search context.
        """
        if self.observation is None:
            return None

        return self.observation.searched_at

    def declared_location(
        self,
    ) -> DeclaredLocation | None:
        """
        Return the structured location declared for the search.
        """
        if self.observation is None:
            return None

        return (
            self.observation
            .declared_location
        )
