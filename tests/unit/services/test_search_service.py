from copy import deepcopy
from uuid import UUID

import pytest

from app.core.errors import QueryProcessingError
from app.core.search_settings import SearchSettings
from app.models.humanitarian_record import HumanitarianRecord
from app.models.query import HumanitarianQuery
from app.services.search import SearchService
from app.storage.base import RecordStorage


VALID_HUMAN_RECORD = {
    "id": "8d4f8fb2-6b13-4ac5-a43b-f34fdd31c101",
    "schema_version": "0.5",
    "source_client": "pytest_search_service",
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
}


def build_query() -> HumanitarianQuery:
    """
    Build a valid human search query.
    """
    return HumanitarianQuery.model_validate(
        {
            "subject": {
                "type": "human",
                "reported_label": "María González",
                "estimated_age": 34,
                "recognition_features": (
                    "cabello negro y chaqueta azul"
                ),
            },
            "observation": {
                "reported_location": "Caracas",
                "searched_at": "2026-08-04T12:00:00Z",
            },
        }
    )


def build_record(
    record_id: str = (
        "8d4f8fb2-6b13-4ac5-a43b-f34fdd31c101"
    ),
    observed_at: str = "2026-07-15T14:30:00Z",
) -> HumanitarianRecord:
    """
    Build a valid matching Humanitarian Record.
    """
    record_data = deepcopy(
        VALID_HUMAN_RECORD
    )

    record_data["id"] = record_id

    record_data[
        "observation"
    ][
        "observed_at"
    ] = observed_at

    return HumanitarianRecord.model_validate(
        record_data
    )


class FakeRecordStorage(RecordStorage):
    """
    In-memory storage double used to verify SearchService interactions.

    list_all() deliberately fails so the tests protect the candidate-search
    architecture from accidentally returning to full-storage reads.
    """

    def __init__(
        self,
        records: list[HumanitarianRecord] | None = None,
        search_error: Exception | None = None,
    ) -> None:
        self.records = list(
            records or []
        )
        self.search_error = search_error
        self.search_calls: list[
            tuple[HumanitarianQuery, int]
        ] = []

    def create(
        self,
        record: HumanitarianRecord,
    ) -> HumanitarianRecord:
        self.records.append(
            record
        )

        return record

    def get_by_id(
        self,
        record_id: UUID,
    ) -> HumanitarianRecord:
        for record in self.records:
            if record.id == record_id:
                return record

        raise LookupError(
            str(record_id)
        )

    def list_all(
        self,
    ) -> list[HumanitarianRecord]:
        raise AssertionError(
            "SearchService must not call list_all()"
        )

    def search_candidates(
        self,
        query: HumanitarianQuery,
        limit: int = 100,
    ) -> list[HumanitarianRecord]:
        self.search_calls.append(
            (
                query,
                limit,
            )
        )

        if self.search_error is not None:
            raise self.search_error

        return list(
            self.records[:limit]
        )

    def exists(
        self,
        record_id: UUID,
    ) -> bool:
        return any(
            record.id == record_id
            for record in self.records
        )


def test_search_service_uses_search_candidates_instead_of_list_all() -> None:
    """
    SearchService must retrieve records exclusively through
    RecordStorage.search_candidates().
    """
    query = build_query()

    storage = FakeRecordStorage(
        records=[
            build_record(),
        ]
    )

    service = SearchService(
        storage=storage,
    )

    results = service.search_records(
        query=query,
    )

    assert len(results) == 1

    assert storage.search_calls == [
        (
            query,
            100,
        ),
    ]


@pytest.mark.parametrize(
    ("result_limit", "expected_candidate_limit"),
    [
        (
            None,
            100,
        ),
        (
            1,
            100,
        ),
        (
            20,
            100,
        ),
        (
            50,
            250,
        ),
        (
            200,
            500,
        ),
        (
            1_000,
            500,
        ),
    ],
)
def test_search_service_calculates_default_candidate_window(
    result_limit: int | None,
    expected_candidate_limit: int,
) -> None:
    """
    Candidate retrieval should use the configured baseline, multiplier and
    maximum.
    """
    storage = FakeRecordStorage()

    service = SearchService(
        storage=storage,
    )

    service.search_records(
        query=build_query(),
        limit=result_limit,
    )

    assert storage.search_calls[
        0
    ][
        1
    ] == expected_candidate_limit


def test_search_service_uses_custom_search_settings() -> None:
    """
    SearchService should honor node-specific candidate-window settings.
    """
    storage = FakeRecordStorage()

    settings = SearchSettings(
        candidate_fetch_limit=40,
        candidate_multiplier=3,
        max_candidate_fetch_limit=120,
    )

    service = SearchService(
        storage=storage,
        settings=settings,
    )

    service.search_records(
        query=build_query(),
        limit=20,
    )

    assert storage.search_calls[
        0
    ][
        1
    ] == 60


def test_search_service_applies_custom_maximum_candidate_limit() -> None:
    """
    The preliminary candidate window must never exceed the configured maximum.
    """
    storage = FakeRecordStorage()

    settings = SearchSettings(
        candidate_fetch_limit=40,
        candidate_multiplier=3,
        max_candidate_fetch_limit=120,
    )

    service = SearchService(
        storage=storage,
        settings=settings,
    )

    service.search_records(
        query=build_query(),
        limit=100,
    )

    assert storage.search_calls[
        0
    ][
        1
    ] == 120


def test_search_service_applies_final_result_limit() -> None:
    """
    The public result limit should be applied after semantic candidate
    evaluation.
    """
    records = [
        build_record(
            record_id=(
                "8d4f8fb2-6b13-4ac5-a43b-f34fdd31c101"
            ),
            observed_at="2026-07-15T14:30:00Z",
        ),
        build_record(
            record_id=(
                "6d13408e-c74f-4584-85b2-8fbf36bb247f"
            ),
            observed_at="2026-07-16T14:30:00Z",
        ),
        build_record(
            record_id=(
                "e01bd61f-d8dd-46fe-b787-07a015fdfb60"
            ),
            observed_at="2026-07-17T14:30:00Z",
        ),
    ]

    storage = FakeRecordStorage(
        records=records,
    )

    service = SearchService(
        storage=storage,
    )

    results = service.search_records(
        query=build_query(),
        limit=2,
    )

    assert len(results) == 2

    assert [
        result.id
        for result in results
    ] == [
        UUID(
            "e01bd61f-d8dd-46fe-b787-07a015fdfb60"
        ),
        UUID(
            "6d13408e-c74f-4584-85b2-8fbf36bb247f"
        ),
    ]

    assert storage.search_calls[
        0
    ][
        1
    ] == 100


@pytest.mark.parametrize(
    "invalid_limit",
    [
        0,
        -1,
        -100,
    ],
)
def test_search_service_rejects_invalid_result_limit(
    invalid_limit: int,
) -> None:
    """
    Invalid public limits should fail before accessing storage.
    """
    storage = FakeRecordStorage()

    service = SearchService(
        storage=storage,
    )

    with pytest.raises(
        QueryProcessingError,
        match=(
            "search limit must be greater than or equal to 1"
        ),
    ):
        service.search_records(
            query=build_query(),
            limit=invalid_limit,
        )

    assert storage.search_calls == []


def test_search_service_wraps_storage_failures() -> None:
    """
    Infrastructure errors should be exposed as QueryProcessingError without
    leaking storage implementation details.
    """
    storage = FakeRecordStorage(
        search_error=RuntimeError(
            "database unavailable"
        )
    )

    service = SearchService(
        storage=storage,
    )

    with pytest.raises(
        QueryProcessingError,
        match=(
            "Unable to process the local Humanitarian Record search"
        ),
    ) as exc_info:
        service.search_records(
            query=build_query(),
        )

    assert isinstance(
        exc_info.value.__cause__,
        RuntimeError,
    )


def test_search_service_filters_incompatible_subject_types() -> None:
    """
    Candidate retrieval may be broad, but SearchService must still reject
    records belonging to another Subject type.
    """
    animal_record = HumanitarianRecord.model_validate(
        {
            "id": "5411ce10-dd1e-4fc8-8201-dda9a23b9212",
            "schema_version": "0.5",
            "source_client": "pytest_search_service",
            "subject": {
                "type": "animal",
                "reported_label": "Luna",
                "recognition_features": (
                    "perra mediana negra con collar rojo"
                ),
            },
            "observation": {
                "event_type": "found",
                "reported_location": "Caracas",
                "reported_by": "volunteer",
                "observed_at": "2026-07-15T16:00:00Z",
            },
        }
    )

    storage = FakeRecordStorage(
        records=[
            animal_record,
        ]
    )

    service = SearchService(
        storage=storage,
    )

    results = service.search_records(
        query=build_query(),
    )

    assert results == []
