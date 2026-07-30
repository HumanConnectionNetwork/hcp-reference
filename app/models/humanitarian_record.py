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


NonEmptyString = Annotated[
    str,
    Field(min_length=1),
]

CanonicalToken = Annotated[
    str,
    Field(
        min_length=1,
        pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$",
    ),
]

CountryCode = Annotated[
    str,
    Field(
        min_length=2,
        max_length=2,
        pattern=r"^[A-Z]{2}$",
        description=(
            "ISO 3166-1 alpha-2 country code."
        ),
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


class DeclaredLocation(HCPModel):
    """
    Human-declared geographic context associated with an observation.

    A Declared Location is not:

    - a GPS coordinate;
    - the current position of the reporting device;
    - an automatically detected location;
    - proof that the Subject was physically present at that location.

    It represents the geographic context voluntarily declared by the person
    or client contributing the Humanitarian Record.

    Geographic levels use internationally neutral field names so HCP does
    not depend on the administrative vocabulary of one country.

    User-facing clients may display localized labels such as:

    - State, Province or Region for admin_level_1;
    - Municipality, County or Department for admin_level_2;
    - City, Town or Community for locality;
    - Neighborhood, Sector or Urbanization for district.
    """

    country_code: CountryCode

    admin_level_1: NonEmptyString

    admin_level_2: NonEmptyString | None = None

    locality: NonEmptyString

    district: NonEmptyString | None = None

    @field_validator("country_code", mode="before")
    @classmethod
    def normalize_country_code(
        cls,
        value: object,
    ) -> object:
        """
        Normalize country codes to uppercase before pattern validation.
        """
        if isinstance(value, str):
            return value.strip().upper()

        return value

    @model_validator(mode="after")
    def normalize_optional_levels(
        self,
    ) -> "DeclaredLocation":
        """
        Convert empty optional geographic levels into None.

        Pydantic strips surrounding whitespace before this validator runs,
        but compatible clients may still send empty strings.
        """
        if self.admin_level_2 == "":
            self.admin_level_2 = None

        if self.district == "":
            self.district = None

        return self

    def to_display_text(
        self,
    ) -> str:
        """
        Build a readable location label from the most specific level upward.

        This method is intended for local presentation and compatibility
        during the transition from schema version 0.5.

        The country remains represented by its ISO code because localization
        belongs to the consuming client, not to the canonical protocol.
        """
        parts = [
            self.district,
            self.locality,
            self.admin_level_2,
            self.admin_level_1,
            self.country_code,
        ]

        return ", ".join(
            part
            for part in parts
            if part
        )

    def hierarchy_tokens(
        self,
    ) -> tuple[str, ...]:
        """
        Return normalized hierarchical values for future spatial comparison.

        The order is broadest to most specific:

        country → first administrative level → second administrative level
        → locality → district.

        These tokens are useful for deterministic filtering and later
        PostgreSQL indexing, but they do not calculate geographic distance.
        """
        values = [
            self.country_code,
            self.admin_level_1,
            self.admin_level_2,
            self.locality,
            self.district,
        ]

        return tuple(
            self._normalize_geographic_value(value)
            for value in values
            if value
        )

    @staticmethod
    def _normalize_geographic_value(
        value: str,
    ) -> str:
        """
        Normalize a geographic value for deterministic internal comparison.
        """
        return " ".join(
            value.casefold().split()
        )


class Subject(HCPModel):
    """
    Living being referenced by one Humanitarian Observation.

    A Subject describes observable humanitarian evidence. It does not
    represent a permanent identity or personal profile.
    """

    type: Literal[
        "human",
        "animal",
    ]

    reported_label: NonEmptyString | None = None

    estimated_age: Annotated[
        int,
        Field(ge=0),
    ] | None = None

    recognition_features: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_subject_fields(
        self,
    ) -> "Subject":
        """
        Ensure that human-only fields are not used for animal Subjects.
        """
        if (
            self.type == "animal"
            and self.estimated_age is not None
        ):
            raise ValueError(
                "estimated_age is applicable only to human Subjects"
            )

        return self


class Observation(HCPModel):
    """
    Humanitarian evidence observed at one specific moment in time.

    The Observation describes:

    - what was observed;
    - who reported it;
    - when it was observed;
    - the geographic context voluntarily declared for that observation;
    - an optional public contact.

    Schema version 0.6 uses declared_location.

    reported_location remains temporarily available only to load and
    process legacy schema version 0.5 records during migration.
    """

    event_type: CanonicalToken

    declared_location: DeclaredLocation | None = None

    reported_location: NonEmptyString | None = Field(
        default=None,
        description=(
            "Legacy free-text location used by schema version 0.5."
        ),
        deprecated=True,
    )

    reported_by: CanonicalToken

    observed_at: datetime

    public_contact: NonEmptyString | None = None

    @field_validator("observed_at")
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
                "observed_at must include an ISO 8601 timezone"
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
        value: datetime,
    ) -> str:
        """
        Serialize the canonical timestamp in UTC using the RFC 3339 Z suffix.
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
    def require_location_context(
        self,
    ) -> "Observation":
        """
        Require structured or legacy geographic context.

        During the migration period:

        - new 0.6 records provide declared_location;
        - legacy 0.5 records may provide reported_location.

        The HumanitarianRecord validator applies the version-specific rule.
        """
        if (
            self.declared_location is None
            and self.reported_location is None
        ):
            raise ValueError(
                "observation must include declared_location or the legacy "
                "reported_location"
            )

        return self

    def location_display_text(
        self,
    ) -> str | None:
        """
        Return a readable location for presentation and legacy services.

        Structured location is always preferred.
        """
        if self.declared_location is not None:
            return (
                self.declared_location
                .to_display_text()
            )

        return self.reported_location


class HumanitarianRecord(HCPModel):
    """
    Canonical representation of one Humanitarian Observation.

    The record identifier identifies this record only. It never identifies
    the human or animal described by the Subject.

    Version transition:

    - schema 0.5:
      accepts the legacy observation.reported_location string;

    - schema 0.6:
      requires observation.declared_location.

    Supporting both versions allows existing JSON records to remain readable
    while the Web, Telegram client and storage layer migrate progressively.
    """

    id: UUID

    schema_version: Literal[
        "0.5",
        "0.6",
    ] = "0.6"

    source_client: NonEmptyString

    subject: Subject

    observation: Observation

    @model_validator(mode="after")
    def validate_schema_location(
        self,
    ) -> "HumanitarianRecord":
        """
        Enforce the location contract associated with each schema version.
        """
        if (
            self.schema_version == "0.6"
            and self.observation.declared_location is None
        ):
            raise ValueError(
                "schema_version 0.6 requires "
                "observation.declared_location"
            )

        if (
            self.schema_version == "0.5"
            and self.observation.reported_location is None
            and self.observation.declared_location is None
        ):
            raise ValueError(
                "schema_version 0.5 requires observation.reported_location "
                "or a compatible declared_location extension"
            )

        return self

    @field_serializer(
        "id",
        when_used="json",
    )
    def serialize_id(
        self,
        value: UUID,
    ) -> str:
        """
        Serialize the record UUID as its canonical lowercase string.
        """
        return str(value)
