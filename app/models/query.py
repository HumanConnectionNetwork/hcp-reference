from typing import Literal

from pydantic import model_validator

from app.models.humanitarian_record import (
    CanonicalToken,
    HCPModel,
    NonEmptyString,
)


class SubjectQuery(HCPModel):
    """
    Humanitarian evidence currently known about the Subject being searched.

    A Subject Query describes partial observable information. It does not
    represent or verify the identity of a human or animal.
    """

    type: Literal["human", "animal"]

    reported_label: NonEmptyString | None = None

    estimated_age: int | None = None

    recognition_features: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_subject_query(self) -> "SubjectQuery":
        """
        Validate Subject-specific Query constraints.
        """
        if self.estimated_age is not None and self.estimated_age < 0:
            raise ValueError("estimated_age must be greater than or equal to 0")

        if self.type == "animal" and self.estimated_age is not None:
            raise ValueError(
                "estimated_age is applicable only to human Subject Queries"
            )

        return self


class ObservationQuery(HCPModel):
    """
    Humanitarian evidence currently known about the event being searched.

    Every field is optional, but an Observation Query must contain at least
    one property when the observation section is supplied.
    """

    event_type: CanonicalToken | None = None

    reported_location: NonEmptyString | None = None

    @model_validator(mode="after")
    def require_observation_evidence(self) -> "ObservationQuery":
        """
        Reject an empty observation object.

        Unknown compatible extension fields are also considered evidence
        because HCP models preserve additional properties.
        """
        known_evidence = (
            self.event_type is not None
            or self.reported_location is not None
        )

        extension_evidence = bool(self.model_extra)

        if not known_evidence and not extension_evidence:
            raise ValueError(
                "observation must contain at least one humanitarian "
                "evidence field"
            )

        return self


class HumanitarianQuery(HCPModel):
    """
    Canonical HCP Query used to search for compatible Humanitarian Records.

    A Query represents the humanitarian evidence currently known by the
    person initiating the search. It requests correlation, not identity
    confirmation or exact matching.
    """

    subject: SubjectQuery

    observation: ObservationQuery | None = None

