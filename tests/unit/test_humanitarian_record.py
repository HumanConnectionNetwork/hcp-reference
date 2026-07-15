from datetime import timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.models.humanitarian_record import HumanitarianRecord


VALID_HUMAN_RECORD = {
    "id": "8d4f8fb2-6b13-4ac5-a43b-f34fdd31c101",
    "schema_version": "0.5",
    "source_client": "pytest_unit",
    "subject": {
        "type": "human",
        "reported_label": "María González",
        "estimated_age": 34,
        "recognition_features": "cabello negro y chaqueta azul",
    },
    "observation": {
        "event_type": "missing",
        "reported_location": "Caracas",
        "reported_by": "family",
        "observed_at": "2026-07-15T14:30:00Z",
        "public_contact": "+58 412 0000000",
    },
}


VALID_ANIMAL_RECORD = {
    "id": "5411ce10-dd1e-4fc8-8201-dda9a23b9212",
    "schema_version": "0.5",
    "source_client": "pytest_unit",
    "subject": {
        "type": "animal",
        "reported_label": "Luna",
        "recognition_features": "perra mediana negra con collar rojo",
    },
    "observation": {
        "event_type": "found",
        "reported_location": "Rio das Ostras",
        "reported_by": "volunteer",
        "observed_at": "2026-07-15T16:00:00-03:00",
    },
}


def test_valid_humanitarian_record_is_created() -> None:
    """
    A complete canonical human record should validate successfully.
    """
    record = HumanitarianRecord.model_validate(VALID_HUMAN_RECORD)

    assert record.id == UUID(
        "8d4f8fb2-6b13-4ac5-a43b-f34fdd31c101"
    )
    assert record.schema_version == "0.5"
    assert record.source_client == "pytest_unit"
    assert record.subject.type == "human"
    assert record.subject.reported_label == "María González"
    assert record.subject.estimated_age == 34
    assert record.observation.event_type == "missing"
    assert record.observation.reported_by == "family"
    assert record.observation.observed_at.tzinfo is not None


def test_valid_animal_record_is_created() -> None:
    """
    A canonical animal record without estimated_age should validate.
    """
    record = HumanitarianRecord.model_validate(VALID_ANIMAL_RECORD)

    assert record.subject.type == "animal"
    assert record.subject.reported_label == "Luna"
    assert record.subject.estimated_age is None
    assert record.observation.event_type == "found"


def test_observed_at_is_normalized_to_utc() -> None:
    """
    Timezone-aware timestamps should be normalized to UTC.
    """
    record = HumanitarianRecord.model_validate(VALID_ANIMAL_RECORD)

    assert record.observation.observed_at.tzinfo == timezone.utc
    assert (
        record.observation.observed_at.isoformat()
        == "2026-07-15T19:00:00+00:00"
    )


def test_record_serializes_uuid_and_timestamp_canonically() -> None:
    """
    JSON serialization should use string UUIDs and UTC timestamps with Z.
    """
    record = HumanitarianRecord.model_validate(VALID_ANIMAL_RECORD)

    serialized = record.model_dump(
        mode="json",
        exclude_none=True,
    )

    assert serialized["id"] == (
        "5411ce10-dd1e-4fc8-8201-dda9a23b9212"
    )
    assert serialized["observation"]["observed_at"] == (
        "2026-07-15T19:00:00Z"
    )


def test_default_schema_version_is_applied() -> None:
    """
    schema_version should default to the current canonical version.
    """
    record_data = {
        **VALID_HUMAN_RECORD,
    }
    record_data.pop("schema_version")

    record = HumanitarianRecord.model_validate(record_data)

    assert record.schema_version == "0.5"


def test_invalid_schema_version_is_rejected() -> None:
    """
    Unsupported schema versions should not validate.
    """
    record_data = {
        **VALID_HUMAN_RECORD,
        "schema_version": "0.4",
    }

    with pytest.raises(ValidationError):
        HumanitarianRecord.model_validate(record_data)


def test_invalid_uuid_is_rejected() -> None:
    """
    Humanitarian Record IDs must be valid UUID values.
    """
    record_data = {
        **VALID_HUMAN_RECORD,
        "id": "not-a-valid-uuid",
    }

    with pytest.raises(ValidationError):
        HumanitarianRecord.model_validate(record_data)


def test_negative_estimated_age_is_rejected() -> None:
    """
    Estimated age cannot be negative.
    """
    record_data = {
        **VALID_HUMAN_RECORD,
        "subject": {
            **VALID_HUMAN_RECORD["subject"],
            "estimated_age": -1,
        },
    }

    with pytest.raises(ValidationError):
        HumanitarianRecord.model_validate(record_data)


def test_estimated_age_is_rejected_for_animal() -> None:
    """
    estimated_age is currently defined only for human Subjects.
    """
    record_data = {
        **VALID_ANIMAL_RECORD,
        "subject": {
            **VALID_ANIMAL_RECORD["subject"],
            "estimated_age": 4,
        },
    }

    with pytest.raises(ValidationError) as exc_info:
        HumanitarianRecord.model_validate(record_data)

    assert "estimated_age is applicable only to human Subjects" in str(
        exc_info.value
    )


def test_timestamp_without_timezone_is_rejected() -> None:
    """
    observed_at must include timezone information.
    """
    record_data = {
        **VALID_HUMAN_RECORD,
        "observation": {
            **VALID_HUMAN_RECORD["observation"],
            "observed_at": "2026-07-15T14:30:00",
        },
    }

    with pytest.raises(ValidationError) as exc_info:
        HumanitarianRecord.model_validate(record_data)

    assert "observed_at must include an ISO 8601 timezone" in str(
        exc_info.value
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_type", "Missing Person"),
        ("event_type", "missing-person"),
        ("reported_by", "Family Member"),
        ("reported_by", "family-member"),
        ("reported_by", ""),
    ],
)
def test_invalid_canonical_tokens_are_rejected(
    field: str,
    value: str,
) -> None:
    """
    Canonical tokens must use lowercase snake_case syntax.
    """
    observation = {
        **VALID_HUMAN_RECORD["observation"],
        field: value,
    }

    record_data = {
        **VALID_HUMAN_RECORD,
        "observation": observation,
    }

    with pytest.raises(ValidationError):
        HumanitarianRecord.model_validate(record_data)


def test_optional_fields_may_be_omitted() -> None:
    """
    Unknown optional evidence should be omitted rather than invented.
    """
    record_data = {
        "id": "c2e238d5-a842-431f-927c-e163b18db33f",
        "schema_version": "0.5",
        "source_client": "pytest_unit",
        "subject": {
            "type": "human",
        },
        "observation": {
            "event_type": "located",
            "reported_by": "hospital",
            "observed_at": "2026-07-15T17:00:00Z",
        },
    }

    record = HumanitarianRecord.model_validate(record_data)

    serialized = record.model_dump(
        mode="json",
        exclude_none=True,
    )

    assert "reported_label" not in serialized["subject"]
    assert "estimated_age" not in serialized["subject"]
    assert "recognition_features" not in serialized["subject"]
    assert "reported_location" not in serialized["observation"]
    assert "public_contact" not in serialized["observation"]


def test_unknown_extension_fields_are_preserved() -> None:
    """
    Compatible extension fields should remain available for protocol
    evolution.
    """
    record_data = {
        **VALID_HUMAN_RECORD,
        "implementation_note": "local extension",
        "subject": {
            **VALID_HUMAN_RECORD["subject"],
            "future_subject_field": "future value",
        },
    }

    record = HumanitarianRecord.model_validate(record_data)

    serialized = record.model_dump(
        mode="json",
        exclude_none=True,
    )

    assert serialized["implementation_note"] == "local extension"
    assert (
        serialized["subject"]["future_subject_field"]
        == "future value"
    )


def test_required_subject_is_rejected_when_missing() -> None:
    """
    Every Humanitarian Record must contain a Subject.
    """
    record_data = {
        key: value
        for key, value in VALID_HUMAN_RECORD.items()
        if key != "subject"
    }

    with pytest.raises(ValidationError):
        HumanitarianRecord.model_validate(record_data)


def test_required_observation_is_rejected_when_missing() -> None:
    """
    Every Humanitarian Record must contain an Observation.
    """
    record_data = {
        key: value
        for key, value in VALID_HUMAN_RECORD.items()
        if key != "observation"
    }

    with pytest.raises(ValidationError):
        HumanitarianRecord.model_validate(record_data)


def test_empty_source_client_is_rejected() -> None:
    """
    source_client must identify the client implementation that created the
    record.
    """
    record_data = {
        **VALID_HUMAN_RECORD,
        "source_client": "   ",
    }

    with pytest.raises(ValidationError):
        HumanitarianRecord.model_validate(record_data)
