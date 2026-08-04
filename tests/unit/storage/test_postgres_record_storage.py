from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import Executable

from app.core.errors import (
    InvalidStorageDataError,
    StorageError,
)
from app.models.humanitarian_record import HumanitarianRecord
from app.models.query import HumanitarianQuery
from app.storage.postgres_store import PostgresRecordStorage


VALID_RECORD_PAYLOAD = {
    "id": "8d4f8fb2-6b13-4ac5-a43b-f34fdd31c101",
    "schema_version": "0.5",
    "source_client": "pytest_postgres_storage",
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
        "reported_by": "family",
        "observed_at": "2026-07-15T14:30:00Z",
        "declared_location": {
            "country_code": "VE",
            "admin_level_1": "Distrito Capital",
            "locality": "Caracas",
        },
    },
}


def build_structured_query() -> HumanitarianQuery:
    """
    Build a query containing structured geographic context.
    """
    return HumanitarianQuery.model_validate(
        {
            "subject": {
                "type": "human",
                "reported_label": "María González",
                "estimated_age": 34,
            },
            "observation": {
                "declared_location": {
                    "country_code": "ve",
                    "admin_level_1": "Distrito Capital",
                    "locality": "Caracas",
                },
                "searched_at": "2026-08-04T12:00:00Z",
            },
        }
    )


def build_legacy_query() -> HumanitarianQuery:
    """
    Build a query containing only a legacy free-text location.
    """
    return HumanitarianQuery.model_validate(
        {
            "subject": {
                "type": "human",
                "reported_label": "María González",
            },
            "observation": {
                "reported_location": "Caracas",
                "searched_at": "2026-08-04T12:00:00Z",
            },
        }
    )


class FakeMappingResult:
    """
    Minimal SQLAlchemy result double supporting mappings().all().
    """

    def __init__(
        self,
        rows: list[Mapping[str, Any]],
    ) -> None:
        self._rows = rows

    def mappings(
        self,
    ) -> "FakeMappingResult":
        return self

    def all(
        self,
    ) -> list[Mapping[str, Any]]:
        return list(
            self._rows
        )


class FakeConnection:
    """
    Connection double that records the executed SQLAlchemy statement.
    """

    def __init__(
        self,
        engine: "FakeEngine",
    ) -> None:
        self.engine = engine

    def __enter__(
        self,
    ) -> "FakeConnection":
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        return None

    def execute(
        self,
        statement: Executable,
    ) -> FakeMappingResult:
        self.engine.executed_statements.append(
            statement
        )

        if self.engine.execute_error is not None:
            raise self.engine.execute_error

        return FakeMappingResult(
            self.engine.rows
        )


class FakeEngine:
    """
    Engine double used to test SQL construction without a PostgreSQL server.
    """

    def __init__(
        self,
        rows: list[Mapping[str, Any]] | None = None,
        execute_error: SQLAlchemyError | None = None,
    ) -> None:
        self.rows = list(
            rows or []
        )

        self.execute_error = execute_error

        self.executed_statements: list[
            Executable
        ] = []

        self.connect_calls = 0
        self.dispose_calls = 0

    def connect(
        self,
    ) -> AbstractContextManager[FakeConnection]:
        self.connect_calls += 1

        return FakeConnection(
            self
        )

    def dispose(
        self,
    ) -> None:
        self.dispose_calls += 1


def compile_statement(
    statement: Executable,
) -> str:
    """
    Compile a SQLAlchemy statement using the PostgreSQL dialect.
    """
    compiled = statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={
            "literal_binds": True,
        },
    )

    return " ".join(
        str(compiled).split()
    )


def build_storage(
    *,
    rows: list[Mapping[str, Any]] | None = None,
    execute_error: SQLAlchemyError | None = None,
) -> tuple[PostgresRecordStorage, FakeEngine]:
    """
    Build PostgreSQL storage with an injected fake Engine.
    """
    engine = FakeEngine(
        rows=rows,
        execute_error=execute_error,
    )

    storage = PostgresRecordStorage(
        engine=engine,  # type: ignore[arg-type]
    )

    return storage, engine


def test_search_candidates_filters_by_subject_type() -> None:
    """
    Candidate SQL must always filter by the requested Subject type.
    """
    storage, engine = build_storage()

    storage.search_candidates(
        query=build_legacy_query(),
        limit=25,
    )

    assert len(
        engine.executed_statements
    ) == 1

    sql = compile_statement(
        engine.executed_statements[
            0
        ]
    )

    assert (
        "humanitarian_records.subject_type = 'human'"
        in sql
    )


def test_search_candidates_applies_structured_country_filter() -> None:
    """
    Structured queries should filter by normalized uppercase country code.
    """
    storage, engine = build_storage()

    storage.search_candidates(
        query=build_structured_query(),
        limit=25,
    )

    sql = compile_statement(
        engine.executed_statements[
            0
        ]
    )

    assert (
        "humanitarian_records.country_code = 'VE'"
        in sql
    )


def test_search_candidates_applies_normalized_admin_level_1_filter() -> None:
    """
    The first administrative level should be normalized before SQL filtering.
    """
    storage, engine = build_storage()

    storage.search_candidates(
        query=build_structured_query(),
        limit=25,
    )

    sql = compile_statement(
        engine.executed_statements[
            0
        ]
    )

    assert (
        "humanitarian_records.admin_level_1_normalized "
        "= 'distrito capital'"
        in sql
    )


def test_search_candidates_does_not_filter_legacy_location_in_sql() -> None:
    """
    Legacy free-text locations should remain available for semantic evaluation
    and must not become an unsafe absolute PostgreSQL filter.
    """
    storage, engine = build_storage()

    storage.search_candidates(
        query=build_legacy_query(),
        limit=25,
    )

    sql = compile_statement(
        engine.executed_statements[
            0
        ]
    )

    assert (
        "country_code ="
        not in sql
    )

    assert (
        "admin_level_1_normalized ="
        not in sql
    )

    assert (
        "legacy_reported_location ="
        not in sql
    )


def test_search_candidates_applies_limit_and_deterministic_order() -> None:
    """
    Candidate SQL must be bounded and ordered by recency and UUID.
    """
    storage, engine = build_storage()

    storage.search_candidates(
        query=build_structured_query(),
        limit=25,
    )

    sql = compile_statement(
        engine.executed_statements[
            0
        ]
    )

    assert (
        "ORDER BY humanitarian_records.observed_at DESC, "
        "humanitarian_records.id ASC"
        in sql
    )

    assert (
        "LIMIT 25"
        in sql
    )


@pytest.mark.parametrize(
    "invalid_limit",
    [
        0,
        -1,
        -100,
    ],
)
def test_search_candidates_rejects_invalid_limit_before_connecting(
    invalid_limit: int,
) -> None:
    """
    Invalid limits should fail without opening a database connection.
    """
    storage, engine = build_storage()

    with pytest.raises(
        ValueError,
        match=(
            "candidate search limit must be greater than or equal to 1"
        ),
    ):
        storage.search_candidates(
            query=build_structured_query(),
            limit=invalid_limit,
        )

    assert engine.connect_calls == 0
    assert engine.executed_statements == []


def test_search_candidates_reconstructs_validated_records() -> None:
    """
    PostgreSQL rows should be reconstructed as validated HCP models.
    """
    record_id = UUID(
        VALID_RECORD_PAYLOAD[
            "id"
        ]
    )

    storage, _ = build_storage(
        rows=[
            {
                "id": record_id,
                "record_payload": VALID_RECORD_PAYLOAD,
            },
        ]
    )

    candidates = storage.search_candidates(
        query=build_structured_query(),
        limit=10,
    )

    assert len(candidates) == 1

    assert isinstance(
        candidates[
            0
        ],
        HumanitarianRecord,
    )

    assert (
        candidates[
            0
        ].id
        == record_id
    )


def test_search_candidates_rejects_payload_with_mismatched_id() -> None:
    """
    The relational UUID and canonical JSON payload UUID must match.
    """
    row_id = UUID(
        "6d13408e-c74f-4584-85b2-8fbf36bb247f"
    )

    storage, _ = build_storage(
        rows=[
            {
                "id": row_id,
                "record_payload": VALID_RECORD_PAYLOAD,
            },
        ]
    )

    with pytest.raises(
        InvalidStorageDataError,
        match=(
            "PostgreSQL row identifier does not match record_payload "
            "identifier"
        ),
    ):
        storage.search_candidates(
            query=build_structured_query(),
            limit=10,
        )


@pytest.mark.parametrize(
    "invalid_payload",
    [
        None,
        [],
        "not-a-json-object",
    ],
)
def test_search_candidates_rejects_non_mapping_payload(
    invalid_payload: object,
) -> None:
    """
    JSONB candidate payloads must contain canonical JSON objects.
    """
    record_id = UUID(
        "8d4f8fb2-6b13-4ac5-a43b-f34fdd31c101"
    )

    storage, _ = build_storage(
        rows=[
            {
                "id": record_id,
                "record_payload": invalid_payload,
            },
        ]
    )

    with pytest.raises(
        InvalidStorageDataError,
        match=(
            "PostgreSQL record_payload must contain a JSON object"
        ),
    ):
        storage.search_candidates(
            query=build_structured_query(),
            limit=10,
        )


def test_search_candidates_wraps_sqlalchemy_errors() -> None:
    """
    SQLAlchemy infrastructure failures should be translated into StorageError.
    """
    storage, _ = build_storage(
        execute_error=SQLAlchemyError(
            "database unavailable"
        )
    )

    with pytest.raises(
        StorageError,
        match=(
            "Unable to search Humanitarian Record candidates in PostgreSQL"
        ),
    ) as exc_info:
        storage.search_candidates(
            query=build_structured_query(),
            limit=10,
        )

    assert isinstance(
        exc_info.value.__cause__,
        SQLAlchemyError,
    )


def test_injected_engine_is_not_disposed_when_storage_closes() -> None:
    """
    PostgresRecordStorage must not dispose an Engine owned by the caller.
    """
    storage, engine = build_storage()

    storage.close()
    storage.close()

    assert engine.dispose_calls == 0


def test_search_candidates_rejects_operations_after_close() -> None:
    """
    A closed PostgreSQL storage instance must reject new candidate searches.
    """
    storage, engine = build_storage()

    storage.close()

    with pytest.raises(
        StorageError,
        match="PostgreSQL storage is closed",
    ):
        storage.search_candidates(
            query=build_structured_query(),
            limit=10,
        )

    assert engine.connect_calls == 0
