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
        description="ISO 3166-1 alpha-2 country code.",
    ),
]


class HCPModel(BaseModel):
    """
    Base model shared by canonical HCP data structures.

    Unknown fields are preserved to support compatible protocol evolution.
    Recognized string values are stripped of surrounding whitespace before
    validation.
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

    It represents geographic context voluntarily declared by the person or
    client contributing the Humanitarian Record.

    Geographic levels use internationally neutral field names:

    - country_code:
      ISO 3166-1 alpha-2 country code;

    - admin_level_1:
      state, province, region or equivalent;

    - admin_level_2:
      municipality, county, department or equivalent;

    - locality:
      city, town, village or community;

    - district:
      neighborhood, sector, parish or urbanization.
    """

    country_code: CountryCode

    admin_level_1: NonEmptyString

    admin_level_2: NonEmptyString | None = None

    locality: NonEmptyString

    district: NonEmptyString | None = None

    @field_validator(
        "country_code",
        mode="before",
    )
    @classmethod
    def normalize_country_code(
        cls,
        value: object,
    ) -> object:
        """
        Normalize country codes to uppercase before validation.
        """
        if isinstance(value, str):
            return value.strip().upper()

        return value

    @field_validator(
        "admin_level_2",
        "district",
        mode="before",
    )
    @classmethod
    def normalize_optional_location_value(
        cls,
        value: object,
    ) -> object:
        """
        Convert empty optional geographic values into None.
        """
        if isinstance(value, str):
            normalized_value = value.strip()

            return normalized_value or None

        return value

    def to_display_text(
        self,
    ) -> str:
        """
        Build a readable location from the most specific level upward.

        Country remains represented by its ISO code because localization
        belongs to the consuming client, not to the HCP contract.
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
        Return normalized geographic values from broadest to most specific.

        Order:

        country
        → first administrative level
        → second administrative level
        → locality
        → district
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
        Normalize one geographic value for deterministic comparison.
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
    Humanitarian evidence observed at one specific moment.

    Schema version 0.6 uses declared_location.

    reported_location remains temporarily available for compatibility with
    schema version 0.5 records and existing clients.

    Location requirements are validated by HumanitarianRecord because they
    depend on schema_version.
    """

    event_type: CanonicalToken

    declared_location: DeclaredLocation | None = None

    reported_location: NonEmptyString | None = Field(
        default=None,
        description=(
            "Legacy free-text geographic context used by schema version 0.5."
        ),
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
        Serialize timestamps in UTC using the RFC 3339 Z suffix.
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
        Return a readable geographic label.

        Structured location is preferred when available. Legacy free text is
        returned for schema version 0.5 records.
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

    The record UUID identifies this record only. It never identifies the
    human or animal described by the Subject.

    Version transition:

    - schema 0.5:
      remains compatible with existing clients and records;
      location may be absent or represented by reported_location;

    - schema 0.6:
      requires observation.declared_location.

    The default remains 0.5 during migration so existing records and clients
    continue working until Web, Telegram and storage are updated.
    """

    id: UUID

    schema_version: Literal[
        "0.5",
        "0.6",
    ] = "0.5"

    source_client: NonEmptyString

    subject: Subject

    observation: Observation

    @model_validator(mode="after")
    def validate_schema_location(
        self,
    ) -> "HumanitarianRecord":
        """
        Enforce the structured location requirement for schema 0.6.

        Migration rules:

        - 0.5 may use reported_location;
        - 0.5 may use declared_location as a compatible extension;
        - 0.5 may omit location;
        - 0.6 requires declared_location.
        """
        if (
            self.schema_version == "0.6"
            and self.observation.declared_location is None
        ):
            raise ValueError(
                "schema_version 0.6 requires "
                "observation.declared_location"
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
