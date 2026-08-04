import json
from pathlib import Path
from uuid import UUID

import pytest

from app.models.humanitarian_record import HumanitarianRecord
from app.models.query import HumanitarianQuery
from app.storage.json_store import JSONRecordStorage


HUMAN_RECORDS = [
    {
        "id": "8d4f8fb2-6b13-4ac5-a43b-f34fdd31c101",
        "schema_version": "0.5",
        "source_client": "pytest_json_storage",
        "subject": {
            "type": "human",
            "reported_label": "María González",
            "estimated_age": 34,
            "recognition_features": (
                "cabello negro y chaqueta azul"
            ),
        },
        "observation": {
            "event_type": "missing",
            "reported_location": "Caracas",
            "reported_by": "family",
            "observed_at": "2026-07-15T14:30:00Z",
        },
    },
    {
        "id": "6d13408e-c74f-4584-85b2-8fbf36bb247f",
        "schema_version": "0.5",
        "source_client": "pytest_json_storage",
        "subject": {
            "type": "human",
            "reported_label": "José Pérez",
            "estimated_age": 62,
            "recognition_features": (
                "camisa blanca y lentes"
            ),
        },
        "observation": {
            "event_type": "hospitalized",
            "reported_location": "Maracaibo",
            "reported_by": "hospital",
            "observed_at": "2026-07-16T09:00:00Z",
        },
    },
    {
        "id": "e01bd61f-d8dd-46fe-b787-07a015fdfb60",
        "schema_version": "0.5",
        "source_client": "pytest_json_storage",
        "subject": {
            "type": "human",
            "reported_label": "Ana Rodríguez",
            "estimated_age": 27,
            "recognition_features": (
                "cabello largo y bolso rojo"
            ),
        },
        "observation": {
            "event_type": "refugee",
            "reported_location": "Valencia",
            "reported_by": "volunteer",
            "observed_at": "2026-07-17T11:15:00Z",
        },
    },
]


ANIMAL_RECORD = {
    "id": "5411ce10-dd1e-4fc8-8201-dda9a23b9212",
    "schema_version": "0.5",
    "source_client": "pytest_json_storage",
    "subject": {
        "type": "animal",
        "reported_label": "Luna",
        "recognition_features": (
            "perra mediana negra con collar rojo"
        ),
    },
    "observation": {
        "event_type": "found",
        "reported_location": "Rio das Ostras",
        "reported_by": "volunteer",
        "observed_at": "2026-07-15T16:00:00-03:00",
    },
}


def write_records(
    file_path: Path,
    records: list[dict[str, object]],
) -> None:
    """
    Write canonical record dictionaries to a temporary JSON storage file.
    """
    file_path.write_text(
        json.dumps(
            records,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def build_human_query() -> HumanitarianQuery:
    """
    Build a valid human search query.
    """
    return HumanitarianQuery.model_validate(
        {
            "subject": {
                "type": "human",
                "reported_label": "María González",
                "estimated_age": 34,
            },
            "observation": {
                "reported_location": "Caracas",
                "searched_at": "2026-08-04T12:00:00Z",
            },
        }
    )


def build_animal_query() -> HumanitarianQuery:
    """
    Build a valid animal search query.
    """
    return HumanitarianQuery.model_validate(
        {
            "subject": {
                "type": "animal",
                "reported_label": "Luna",
                "species": "dog",
            },
            "observation": {
                "reported_location": "Rio das Ostras",
                "searched_at": "2026-08-04T12:00:00Z",
            },
        }
    )


def test_search_candidates_respects_limit(
    tmp_path: Path,
) -> None:
    """
    JSON candidate retrieval should return no more than the requested limit.
    """
    storage_path = (
        tmp_path
        / "hcp_records.json"
    )

    write_records(
        storage_path,
        HUMAN_RECORDS,
    )

    storage = JSONRecordStorage(
        file_path=storage_path,
    )

    candidates = storage.search_candidates(
        query=build_human_query(),
        limit=2,
    )

    assert len(candidates) == 2


def test_search_candidates_preserves_file_order(
    tmp_path: Path,
) -> None:
    """
    JSON candidate retrieval should preserve the order stored in the file.
    """
    storage_path = (
        tmp_path
        / "hcp_records.json"
    )

    write_records(
        storage_path,
        HUMAN_RECORDS,
    )

    storage = JSONRecordStorage(
        file_path=storage_path,
    )

    candidates = storage.search_candidates(
        query=build_human_query(),
        limit=3,
    )

    assert [
        candidate.id
        for candidate in candidates
    ] == [
        UUID(
            "8d4f8fb2-6b13-4ac5-a43b-f34fdd31c101"
        ),
        UUID(
            "6d13408e-c74f-4584-85b2-8fbf36bb247f"
        ),
        UUID(
            "e01bd61f-d8dd-46fe-b787-07a015fdfb60"
        ),
    ]


def test_search_candidates_returns_validated_models(
    tmp_path: Path,
) -> None:
    """
    Candidate retrieval should return HumanitarianRecord models rather than
    unvalidated dictionaries.
    """
    storage_path = (
        tmp_path
        / "hcp_records.json"
    )

    write_records(
        storage_path,
        HUMAN_RECORDS,
    )

    storage = JSONRecordStorage(
        file_path=storage_path,
    )

    candidates = storage.search_candidates(
        query=build_human_query(),
        limit=3,
    )

    assert candidates

    assert all(
        isinstance(
            candidate,
            HumanitarianRecord,
        )
        for candidate in candidates
    )


@pytest.mark.parametrize(
    "invalid_limit",
    [
        0,
        -1,
        -100,
    ],
)
def test_search_candidates_rejects_invalid_limit(
    tmp_path: Path,
    invalid_limit: int,
) -> None:
    """
    Candidate retrieval limits must always be greater than or equal to one.
    """
    storage_path = (
        tmp_path
        / "hcp_records.json"
    )

    write_records(
        storage_path,
        HUMAN_RECORDS,
    )

    storage = JSONRecordStorage(
        file_path=storage_path,
    )

    with pytest.raises(
        ValueError,
        match=(
            "candidate search limit must be greater than or equal to 1"
        ),
    ):
        storage.search_candidates(
            query=build_human_query(),
            limit=invalid_limit,
        )


def test_search_candidates_does_not_modify_persisted_records(
    tmp_path: Path,
) -> None:
    """
    Modifying a returned model should not change the JSON file or subsequent
    storage reads.
    """
    storage_path = (
        tmp_path
        / "hcp_records.json"
    )

    write_records(
        storage_path,
        HUMAN_RECORDS,
    )

    storage = JSONRecordStorage(
        file_path=storage_path,
    )

    first_candidates = storage.search_candidates(
        query=build_human_query(),
        limit=1,
    )

    first_candidates[
        0
    ].subject.reported_label = "Changed locally"

    second_candidates = storage.search_candidates(
        query=build_human_query(),
        limit=1,
    )

    assert (
        second_candidates[
            0
        ].subject.reported_label
        == "María González"
    )

    persisted_data = json.loads(
        storage_path.read_text(
            encoding="utf-8",
        )
    )

    assert (
        persisted_data[
            0
        ][
            "subject"
        ][
            "reported_label"
        ]
        == "María González"
    )


def test_search_candidates_keeps_json_compatibility_for_animal_queries(
    tmp_path: Path,
) -> None:
    """
    JSON storage should accept animal queries while leaving semantic filtering
    to SearchService.
    """
    storage_path = (
        tmp_path
        / "hcp_records.json"
    )

    records = [
        HUMAN_RECORDS[
            0
        ],
        ANIMAL_RECORD,
    ]

    write_records(
        storage_path,
        records,
    )

    storage = JSONRecordStorage(
        file_path=storage_path,
    )

    candidates = storage.search_candidates(
        query=build_animal_query(),
        limit=10,
    )

    assert len(candidates) == 2

    assert [
        candidate.subject.type
        for candidate in candidates
    ] == [
        "human",
        "animal",
    ]


def test_search_candidates_returns_all_records_when_limit_is_larger(
    tmp_path: Path,
) -> None:
    """
    A limit larger than the stored collection should return the complete
    collection without adding or duplicating records.
    """
    storage_path = (
        tmp_path
        / "hcp_records.json"
    )

    write_records(
        storage_path,
        HUMAN_RECORDS,
    )

    storage = JSONRecordStorage(
        file_path=storage_path,
    )

    candidates = storage.search_candidates(
        query=build_human_query(),
        limit=100,
    )

    assert len(candidates) == 3

    assert len(
        {
            candidate.id
            for candidate in candidates
        }
    ) == 3
