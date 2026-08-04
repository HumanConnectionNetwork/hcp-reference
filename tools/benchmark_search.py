#!/usr/bin/env python3
"""
Official HCP search benchmark.

The tool supports four actions:

- lifecycle:
    Generate a temporary dataset, prepare storage, run the complete HCP search
    pipeline and remove the dataset.

- prepare:
    Create a named reusable benchmark dataset.

- search:
    Run only the HCP search pipeline against a previously prepared dataset.

- cleanup:
    Remove a previously prepared dataset.

Legacy compatibility
--------------------

The former arguments remain accepted:

    --mode lifecycle
    --mode search-only

They map to:

    --action lifecycle

A reusable dataset should use the explicit actions:

    python tools/benchmark_search.py \
        --action prepare \
        --storage postgres \
        --records 100000 \
        --dataset benchmark100k

    python tools/benchmark_search.py \
        --action search \
        --storage postgres \
        --dataset benchmark100k \
        --repeats 20

    python tools/benchmark_search.py \
        --action cleanup \
        --storage postgres \
        --dataset benchmark100k

Safety
------

- Repository-level .env values are loaded automatically.
- Existing process environment variables take precedence over .env.
- PostgreSQL datasets are identified by source_client and a dataset-specific
  synthetic geographic context.
- JSON reusable datasets are stored under benchmark_datasets/ by default.
- Prepared datasets are never removed by the search action.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )


from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from app.core.search_settings import SearchSettings  # noqa: E402
from app.models.humanitarian_record import HumanitarianRecord  # noqa: E402
from app.models.query import HumanitarianQuery  # noqa: E402
from app.services.case_builder import HumanitarianCaseBuilder  # noqa: E402
from app.services.correlation import CorrelationService  # noqa: E402
from app.services.search import SearchService  # noqa: E402
from app.storage.base import RecordStorage  # noqa: E402
from app.storage.json_store import JSONRecordStorage  # noqa: E402
from app.storage.postgres_store import (  # noqa: E402
    PostgresRecordStorage,
    humanitarian_records_table,
    metadata,
)


BENCHMARK_SOURCE_PREFIX = "hcp_benchmark_search"
DEFAULT_DATASET_DIRECTORY = PROJECT_ROOT / "benchmark_datasets"
DEFAULT_OUTPUT_DIRECTORY = PROJECT_ROOT / "benchmark_results"
DATASET_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
SYNTHETIC_COUNTRY_CODE = "XZ"


@dataclass(frozen=True, slots=True)
class IterationMetrics:
    candidate_search_ms: float
    candidate_evaluation_ms: float
    correlation_ms: float
    case_builder_ms: float
    total_ms: float
    candidates_fetched: int
    search_results: int
    correlation_results: int
    case_built: bool


@dataclass(frozen=True, slots=True)
class MetricStatistics:
    median_ms: float
    minimum_ms: float
    maximum_ms: float
    mean_ms: float
    standard_deviation_ms: float


@dataclass(frozen=True, slots=True)
class BenchmarkSummary:
    action: str
    storage: str
    dataset: str
    records: int
    repeats: int
    warmups: int
    result_limit: int
    candidate_fetch_limit: int
    candidate_multiplier: int
    max_candidate_fetch_limit: int
    candidate_search: MetricStatistics
    candidate_evaluation: MetricStatistics
    correlation: MetricStatistics
    case_builder: MetricStatistics
    total_pipeline: MetricStatistics
    candidates_fetched: int
    search_results: int
    correlation_results: int
    case_built: bool
    generated_at: str
    dataset_generation_ms: float | None = None
    storage_preparation_ms: float | None = None
    cleanup_ms: float | None = None
    lifecycle_total_ms: float | None = None


class TimedRecordStorage(RecordStorage):
    """
    Transparent RecordStorage decorator that measures candidate retrieval.
    """

    def __init__(
        self,
        storage: RecordStorage,
    ) -> None:
        self.storage = storage
        self.last_candidate_search_seconds = 0.0
        self.last_candidate_count = 0

    def create(
        self,
        record: HumanitarianRecord,
    ) -> HumanitarianRecord:
        return self.storage.create(record)

    def get_by_id(
        self,
        record_id: UUID,
    ) -> HumanitarianRecord:
        return self.storage.get_by_id(record_id)

    def list_all(
        self,
    ) -> list[HumanitarianRecord]:
        return self.storage.list_all()

    def search_candidates(
        self,
        query: HumanitarianQuery,
        limit: int = 100,
    ) -> list[HumanitarianRecord]:
        started_at = time.perf_counter()

        records = self.storage.search_candidates(
            query=query,
            limit=limit,
        )

        self.last_candidate_search_seconds = (
            time.perf_counter()
            - started_at
        )
        self.last_candidate_count = len(records)

        return records

    def exists(
        self,
        record_id: UUID,
    ) -> bool:
        return self.storage.exists(record_id)

    def create_many(
        self,
        records: list[HumanitarianRecord],
    ) -> list[HumanitarianRecord]:
        return self.storage.create_many(records)

    def count(
        self,
    ) -> int:
        return self.storage.count()

    def close(
        self,
    ) -> None:
        self.storage.close()


def load_local_environment() -> None:
    """
    Load simple KEY=VALUE entries from the repository-level .env file.

    Existing process environment variables always take precedence.
    """
    env_path = PROJECT_ROOT / ".env"

    if not env_path.exists():
        return

    for raw_line in env_path.read_text(
        encoding="utf-8",
    ).splitlines():
        line = raw_line.strip()

        if (
            not line
            or line.startswith("#")
            or "=" not in line
        ):
            continue

        name, value = line.split(
            "=",
            1,
        )

        name = name.strip()
        value = value.strip().strip('"').strip("'")

        if name:
            os.environ.setdefault(
                name,
                value,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare reusable HCP benchmark datasets, run search-only "
            "measurements, clean datasets or execute the complete lifecycle."
        )
    )

    parser.add_argument(
        "--action",
        choices=(
            "lifecycle",
            "prepare",
            "search",
            "cleanup",
        ),
        default=None,
        help=(
            "Operation to execute. Default: lifecycle. "
            "Use prepare/search/cleanup for reusable datasets."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=(
            "lifecycle",
            "search-only",
        ),
        default=None,
        help=(
            "Deprecated compatibility option. Both values execute a temporary "
            "lifecycle. Use --action prepare/search/cleanup for persistent "
            "datasets."
        ),
    )
    parser.add_argument(
        "--storage",
        choices=(
            "json",
            "postgres",
        ),
        default="json",
        help="Storage backend. Default: json.",
    )
    parser.add_argument(
        "--dataset",
        default=None,
        help=(
            "Reusable dataset name. Required for prepare, search and cleanup. "
            "Use letters, numbers, dots, underscores or hyphens."
        ),
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=DEFAULT_DATASET_DIRECTORY,
        help=(
            "Directory used for reusable JSON datasets. "
            "Default: benchmark_datasets."
        ),
    )
    parser.add_argument(
        "--records",
        type=int,
        default=10_000,
        help=(
            "Records generated by lifecycle or prepare. "
            "Default: 10000."
        ),
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=5,
        help="Measured search executions. Default: 5.",
    )
    parser.add_argument(
        "--warmups",
        type=int,
        default=1,
        help="Unmeasured warm-up executions. Default: 1.",
    )
    parser.add_argument(
        "--result-limit",
        type=int,
        default=20,
        help="Final SearchService result limit. Default: 20.",
    )
    parser.add_argument(
        "--candidate-fetch-limit",
        type=int,
        default=100,
        help="Baseline candidate window. Default: 100.",
    )
    parser.add_argument(
        "--candidate-multiplier",
        type=int,
        default=5,
        help="Candidate multiplier. Default: 5.",
    )
    parser.add_argument(
        "--max-candidate-fetch-limit",
        type=int,
        default=500,
        help="Maximum candidate window. Default: 500.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help=(
            "PostgreSQL URL. When omitted, DATABASE_URL is used."
        ),
    )
    parser.add_argument(
        "--initialize-schema",
        action="store_true",
        help=(
            "Create the PostgreSQL table and indexes when absent. "
            "Use only with disposable or development databases."
        ),
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help=(
            "Replace an existing named dataset during prepare."
        ),
    )
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help=(
            "Keep the temporary PostgreSQL dataset after lifecycle. "
            "Named prepared datasets are always kept until cleanup."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON result path.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260804,
        help="Deterministic dataset seed. Default: 20260804.",
    )

    args = parser.parse_args()

    if args.action is None:
        args.action = "lifecycle"

    if args.mode is not None:
        if args.action != "lifecycle":
            parser.error(
                "--mode cannot be combined with a non-lifecycle --action"
            )

        # Legacy search-only prepared data inside the same execution, so it is
        # still mapped to lifecycle. The reported pipeline remains search-only.
        args.action = "lifecycle"

    if (
        args.action
        in {
            "prepare",
            "search",
            "cleanup",
        }
        and not args.dataset
    ):
        parser.error(
            "--dataset is required for prepare, search and cleanup"
        )

    if args.dataset is not None:
        validate_dataset_name(
            args.dataset,
            parser=parser,
        )

    positive_fields = {
        "--records": args.records,
        "--repeats": args.repeats,
        "--result-limit": args.result_limit,
        "--candidate-fetch-limit": args.candidate_fetch_limit,
        "--candidate-multiplier": args.candidate_multiplier,
        "--max-candidate-fetch-limit": args.max_candidate_fetch_limit,
    }

    for field_name, value in positive_fields.items():
        if value < 1:
            parser.error(
                f"{field_name} must be greater than or equal to 1"
            )

    if args.warmups < 0:
        parser.error(
            "--warmups must be greater than or equal to 0"
        )

    if (
        args.max_candidate_fetch_limit
        < args.candidate_fetch_limit
    ):
        parser.error(
            "--max-candidate-fetch-limit must be greater than or equal to "
            "--candidate-fetch-limit"
        )

    return args


def validate_dataset_name(
    dataset: str,
    *,
    parser: argparse.ArgumentParser | None = None,
) -> None:
    if DATASET_NAME_PATTERN.fullmatch(
        dataset
    ):
        return

    message = (
        "dataset name must start with a letter or number and contain only "
        "letters, numbers, dots, underscores or hyphens; maximum length is 80"
    )

    if parser is not None:
        parser.error(
            message
        )

    raise ValueError(
        message
    )


def dataset_source_client(
    dataset: str,
) -> str:
    return (
        f"{BENCHMARK_SOURCE_PREFIX}:"
        f"{dataset}"
    )


def dataset_admin_level_1(
    dataset: str,
) -> str:
    digest = uuid5(
        NAMESPACE_URL,
        dataset,
    ).hex[:16]

    return f"HCP Benchmark {digest}"


def json_dataset_path(
    dataset_directory: Path,
    dataset: str,
) -> Path:
    return (
        dataset_directory
        / f"{dataset}.json"
    )


def build_query(
    dataset: str,
) -> HumanitarianQuery:
    return HumanitarianQuery.model_validate(
        {
            "query_id": str(
                uuid5(
                    NAMESPACE_URL,
                    f"hcp-benchmark-query:{dataset}",
                )
            ),
            "subject": {
                "type": "human",
                "reported_label": "María González",
                "estimated_age": 34,
                "recognition_features": (
                    "cabello negro chaqueta azul cicatriz ceja derecha"
                ),
            },
            "observation": {
                "declared_location": {
                    "country_code": SYNTHETIC_COUNTRY_CODE,
                    "admin_level_1": dataset_admin_level_1(
                        dataset
                    ),
                    "admin_level_2": "Benchmark",
                    "locality": "HCP Synthetic City",
                    "district": "Benchmark District",
                },
                "searched_at": "2026-08-04T12:00:00Z",
            },
        }
    )


def generate_records(
    count: int,
    *,
    seed: int,
    dataset: str,
) -> list[HumanitarianRecord]:
    """
    Generate deterministic canonical records isolated by dataset geography.
    """
    records: list[HumanitarianRecord] = []
    source_client = dataset_source_client(
        dataset
    )
    admin_level_1 = dataset_admin_level_1(
        dataset
    )
    base_time = datetime(
        2026,
        8,
        4,
        11,
        0,
        tzinfo=timezone.utc,
    )

    for index in range(
        count
    ):
        matching_description = (
            index % 100 == 0
        )

        if matching_description:
            reported_label = "María González"
            estimated_age = 34
            recognition_features = (
                "cabello negro chaqueta azul cicatriz ceja derecha"
            )
        else:
            reported_label = (
                f"Persona Benchmark {index:08d}"
            )
            estimated_age = (
                18
                + (
                    (
                        index
                        + seed
                    )
                    % 70
                )
            )
            recognition_features = (
                f"descripcion sintetica benchmark numero {index}"
            )

        record_id = uuid5(
            NAMESPACE_URL,
            (
                f"hcp-benchmark-record:"
                f"{dataset}:"
                f"{seed}:"
                f"{index}"
            ),
        )

        records.append(
            HumanitarianRecord.model_validate(
                {
                    "id": str(
                        record_id
                    ),
                    "schema_version": "0.6",
                    "source_client": source_client,
                    "subject": {
                        "type": "human",
                        "reported_label": reported_label,
                        "estimated_age": estimated_age,
                        "recognition_features": recognition_features,
                    },
                    "observation": {
                        "event_type": (
                            "missing"
                            if index % 2 == 0
                            else "hospitalized"
                        ),
                        "reported_by": "volunteer",
                        "observed_at": (
                            base_time
                            - timedelta(
                                minutes=index
                            )
                        ).isoformat(),
                        "declared_location": {
                            "country_code": SYNTHETIC_COUNTRY_CODE,
                            "admin_level_1": admin_level_1,
                            "admin_level_2": "Benchmark",
                            "locality": "HCP Synthetic City",
                            "district": "Benchmark District",
                        },
                    },
                }
            )
        )

    return records


def write_json_dataset(
    path: Path,
    records: list[HumanitarianRecord],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = path.with_suffix(
        f"{path.suffix}.tmp"
    )

    temporary_path.write_text(
        json.dumps(
            [
                record.model_dump(
                    mode="json",
                    exclude_none=True,
                )
                for record in records
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    temporary_path.replace(
        path
    )


def resolve_database_url(
    args: argparse.Namespace,
) -> str:
    database_url = (
        args.database_url
        or os.getenv(
            "DATABASE_URL"
        )
    )

    if not database_url:
        raise SystemExit(
            "PostgreSQL action requires --database-url or DATABASE_URL."
        )

    return database_url


def open_postgres_storage(
    args: argparse.Namespace,
) -> PostgresRecordStorage:
    storage = PostgresRecordStorage(
        database_url=resolve_database_url(
            args
        ),
        application_name="hcp-reference-benchmark",
    )

    if args.initialize_schema:
        metadata.create_all(
            storage.engine
        )

    storage.ping()

    return storage


def postgres_dataset_count(
    storage: PostgresRecordStorage,
    dataset: str,
) -> int:
    statement = select(
        func.count()
    ).select_from(
        humanitarian_records_table
    ).where(
        humanitarian_records_table.c.source_client
        == dataset_source_client(
            dataset
        )
    )

    try:
        with storage.engine.connect() as connection:
            value = connection.execute(
                statement
            ).scalar_one()

        return int(
            value
        )

    except SQLAlchemyError as exc:
        raise SystemExit(
            f"Unable to count PostgreSQL benchmark dataset: {exc}"
        ) from exc


def cleanup_postgres_dataset(
    storage: PostgresRecordStorage,
    dataset: str,
) -> int:
    statement = delete(
        humanitarian_records_table
    ).where(
        humanitarian_records_table.c.source_client
        == dataset_source_client(
            dataset
        )
    )

    try:
        with storage.engine.begin() as connection:
            result = connection.execute(
                statement
            )

        return int(
            result.rowcount
            if result.rowcount is not None
            else 0
        )

    except SQLAlchemyError as exc:
        raise SystemExit(
            f"Unable to remove PostgreSQL benchmark dataset: {exc}"
        ) from exc


def insert_postgres_records(
    storage: PostgresRecordStorage,
    records: list[HumanitarianRecord],
) -> None:
    batch_size = 2_000

    for start in range(
        0,
        len(records),
        batch_size,
    ):
        storage.create_many(
            records[
                start:
                start + batch_size
            ]
        )


def prepare_dataset(
    args: argparse.Namespace,
) -> None:
    assert args.dataset is not None

    started_at = time.perf_counter()

    if args.storage == "json":
        path = json_dataset_path(
            args.dataset_dir,
            args.dataset,
        )

        if path.exists() and not args.replace:
            raise SystemExit(
                f"Dataset already exists: {path}. Use --replace to overwrite."
            )

        generation_started_at = time.perf_counter()
        records = generate_records(
            args.records,
            seed=args.seed,
            dataset=args.dataset,
        )
        generation_ms = (
            time.perf_counter()
            - generation_started_at
        ) * 1_000

        preparation_started_at = time.perf_counter()
        write_json_dataset(
            path,
            records,
        )
        preparation_ms = (
            time.perf_counter()
            - preparation_started_at
        ) * 1_000

        print()
        print("HCP Benchmark Dataset Prepared")
        print("=" * 56)
        print(f"Storage                  : json")
        print(f"Dataset                  : {args.dataset}")
        print(f"Records                  : {args.records:,}")
        print(f"Path                     : {path.resolve()}")
        print(f"Dataset generation       : {generation_ms:10.3f} ms")
        print(f"Storage preparation      : {preparation_ms:10.3f} ms")
        print(
            f"Total elapsed            : "
            f"{(time.perf_counter() - started_at) * 1_000:10.3f} ms"
        )
        print("=" * 56)
        return

    storage = open_postgres_storage(
        args
    )

    try:
        existing_count = postgres_dataset_count(
            storage,
            args.dataset,
        )

        if existing_count:
            if not args.replace:
                raise SystemExit(
                    f"Dataset '{args.dataset}' already contains "
                    f"{existing_count:,} PostgreSQL records. "
                    "Use --replace to recreate it."
                )

            removed = cleanup_postgres_dataset(
                storage,
                args.dataset,
            )

            print(
                f"Removed {removed:,} existing dataset records."
            )

        generation_started_at = time.perf_counter()
        records = generate_records(
            args.records,
            seed=args.seed,
            dataset=args.dataset,
        )
        generation_ms = (
            time.perf_counter()
            - generation_started_at
        ) * 1_000

        preparation_started_at = time.perf_counter()
        insert_postgres_records(
            storage,
            records,
        )
        preparation_ms = (
            time.perf_counter()
            - preparation_started_at
        ) * 1_000

        persisted_count = postgres_dataset_count(
            storage,
            args.dataset,
        )

        print()
        print("HCP Benchmark Dataset Prepared")
        print("=" * 56)
        print(f"Storage                  : postgres")
        print(f"Dataset                  : {args.dataset}")
        print(f"Records requested        : {args.records:,}")
        print(f"Records persisted        : {persisted_count:,}")
        print(f"Dataset generation       : {generation_ms:10.3f} ms")
        print(f"Storage preparation      : {preparation_ms:10.3f} ms")
        print(
            f"Total elapsed            : "
            f"{(time.perf_counter() - started_at) * 1_000:10.3f} ms"
        )
        print("=" * 56)

    finally:
        storage.close()


def open_prepared_dataset(
    args: argparse.Namespace,
) -> tuple[RecordStorage, int]:
    assert args.dataset is not None

    if args.storage == "json":
        path = json_dataset_path(
            args.dataset_dir,
            args.dataset,
        )

        if not path.exists():
            raise SystemExit(
                f"JSON dataset does not exist: {path}. "
                "Run --action prepare first."
            )

        storage = JSONRecordStorage(
            file_path=path,
        )

        return (
            storage,
            storage.count(),
        )

    storage = open_postgres_storage(
        args
    )
    count = postgres_dataset_count(
        storage,
        args.dataset,
    )

    if count < 1:
        storage.close()

        raise SystemExit(
            f"PostgreSQL dataset '{args.dataset}' does not exist or is empty. "
            "Run --action prepare first."
        )

    return (
        storage,
        count,
    )


def cleanup_dataset(
    args: argparse.Namespace,
) -> None:
    assert args.dataset is not None

    if args.storage == "json":
        path = json_dataset_path(
            args.dataset_dir,
            args.dataset,
        )

        if not path.exists():
            raise SystemExit(
                f"JSON dataset does not exist: {path}"
            )

        path.unlink()

        print(
            f"Removed JSON benchmark dataset: {path.resolve()}"
        )
        return

    storage = open_postgres_storage(
        args
    )

    try:
        removed = cleanup_postgres_dataset(
            storage,
            args.dataset,
        )

        print(
            f"Removed {removed:,} PostgreSQL records "
            f"from dataset '{args.dataset}'."
        )

    finally:
        storage.close()


def execute_pipeline(
    *,
    timed_storage: TimedRecordStorage,
    query: HumanitarianQuery,
    settings: SearchSettings,
    result_limit: int,
) -> IterationMetrics:
    search_service = SearchService(
        storage=timed_storage,
        settings=settings,
    )
    correlation_service = CorrelationService()
    case_builder = HumanitarianCaseBuilder()

    total_started_at = time.perf_counter()

    search_started_at = time.perf_counter()
    records = search_service.search_records(
        query=query,
        limit=result_limit,
    )
    search_seconds = (
        time.perf_counter()
        - search_started_at
    )

    candidate_search_seconds = (
        timed_storage.last_candidate_search_seconds
    )
    candidate_evaluation_seconds = max(
        0.0,
        search_seconds
        - candidate_search_seconds,
    )

    correlation_started_at = time.perf_counter()
    correlations = correlation_service.correlate_records(
        query=query,
        records=records,
        limit=result_limit,
    )
    correlation_seconds = (
        time.perf_counter()
        - correlation_started_at
    )

    case_builder_seconds = 0.0
    case_built = False

    if correlations:
        case_started_at = time.perf_counter()

        case_builder.build(
            query=query,
            results=correlations,
            records=records,
        )

        case_builder_seconds = (
            time.perf_counter()
            - case_started_at
        )
        case_built = True

    total_seconds = (
        time.perf_counter()
        - total_started_at
    )

    return IterationMetrics(
        candidate_search_ms=(
            candidate_search_seconds
            * 1_000
        ),
        candidate_evaluation_ms=(
            candidate_evaluation_seconds
            * 1_000
        ),
        correlation_ms=(
            correlation_seconds
            * 1_000
        ),
        case_builder_ms=(
            case_builder_seconds
            * 1_000
        ),
        total_ms=(
            total_seconds
            * 1_000
        ),
        candidates_fetched=(
            timed_storage.last_candidate_count
        ),
        search_results=len(
            records
        ),
        correlation_results=len(
            correlations
        ),
        case_built=case_built,
    )


def metric_statistics(
    values: list[float],
) -> MetricStatistics:
    return MetricStatistics(
        median_ms=float(
            statistics.median(
                values
            )
        ),
        minimum_ms=min(
            values
        ),
        maximum_ms=max(
            values
        ),
        mean_ms=float(
            statistics.fmean(
                values
            )
        ),
        standard_deviation_ms=(
            float(
                statistics.stdev(
                    values
                )
            )
            if len(values) > 1
            else 0.0
        ),
    )


def summarize(
    *,
    action: str,
    storage: str,
    dataset: str,
    records: int,
    args: argparse.Namespace,
    settings: SearchSettings,
    metrics: list[IterationMetrics],
    dataset_generation_ms: float | None = None,
    storage_preparation_ms: float | None = None,
    cleanup_ms: float | None = None,
    lifecycle_total_ms: float | None = None,
) -> BenchmarkSummary:
    representative = metrics[
        len(metrics)
        // 2
    ]

    return BenchmarkSummary(
        action=action,
        storage=storage,
        dataset=dataset,
        records=records,
        repeats=args.repeats,
        warmups=args.warmups,
        result_limit=args.result_limit,
        candidate_fetch_limit=(
            settings.candidate_fetch_limit
        ),
        candidate_multiplier=(
            settings.candidate_multiplier
        ),
        max_candidate_fetch_limit=(
            settings.max_candidate_fetch_limit
        ),
        candidate_search=metric_statistics(
            [
                metric.candidate_search_ms
                for metric in metrics
            ]
        ),
        candidate_evaluation=metric_statistics(
            [
                metric.candidate_evaluation_ms
                for metric in metrics
            ]
        ),
        correlation=metric_statistics(
            [
                metric.correlation_ms
                for metric in metrics
            ]
        ),
        case_builder=metric_statistics(
            [
                metric.case_builder_ms
                for metric in metrics
            ]
        ),
        total_pipeline=metric_statistics(
            [
                metric.total_ms
                for metric in metrics
            ]
        ),
        candidates_fetched=int(
            statistics.median(
                metric.candidates_fetched
                for metric in metrics
            )
        ),
        search_results=int(
            statistics.median(
                metric.search_results
                for metric in metrics
            )
        ),
        correlation_results=int(
            statistics.median(
                metric.correlation_results
                for metric in metrics
            )
        ),
        case_built=representative.case_built,
        generated_at=datetime.now(
            timezone.utc
        ).isoformat(),
        dataset_generation_ms=dataset_generation_ms,
        storage_preparation_ms=storage_preparation_ms,
        cleanup_ms=cleanup_ms,
        lifecycle_total_ms=lifecycle_total_ms,
    )


def print_metric(
    label: str,
    metric: MetricStatistics,
) -> None:
    print(
        f"{label:<25}: "
        f"{metric.median_ms:10.3f} ms median "
        f"[{metric.minimum_ms:.3f}–{metric.maximum_ms:.3f}]"
    )


def print_summary(
    summary: BenchmarkSummary,
) -> None:
    print()
    print("HCP Search Benchmark")
    print("=" * 72)
    print(f"Action                   : {summary.action}")
    print(f"Storage                  : {summary.storage}")
    print(f"Dataset                  : {summary.dataset}")
    print(f"Dataset records          : {summary.records:,}")
    print(f"Measured repetitions     : {summary.repeats}")
    print(f"Warm-up repetitions      : {summary.warmups}")
    print(f"Final result limit       : {summary.result_limit}")
    print(f"Candidates fetched       : {summary.candidates_fetched}")
    print(f"Search results           : {summary.search_results}")
    print(f"Correlation results      : {summary.correlation_results}")
    print(f"Humanitarian Case built  : {summary.case_built}")
    print("-" * 72)
    print_metric(
        "Candidate search",
        summary.candidate_search,
    )
    print_metric(
        "Candidate evaluation",
        summary.candidate_evaluation,
    )
    print_metric(
        "Correlation",
        summary.correlation,
    )
    print_metric(
        "Case builder",
        summary.case_builder,
    )
    print_metric(
        "Total pipeline",
        summary.total_pipeline,
    )

    if summary.action == "lifecycle":
        print("-" * 72)

        if summary.dataset_generation_ms is not None:
            print(
                f"Dataset generation       : "
                f"{summary.dataset_generation_ms:10.3f} ms"
            )

        if summary.storage_preparation_ms is not None:
            print(
                f"Storage preparation      : "
                f"{summary.storage_preparation_ms:10.3f} ms"
            )

        if summary.cleanup_ms is not None:
            print(
                f"Cleanup                  : "
                f"{summary.cleanup_ms:10.3f} ms"
            )

        if summary.lifecycle_total_ms is not None:
            print(
                f"Lifecycle elapsed        : "
                f"{summary.lifecycle_total_ms:10.3f} ms"
            )

    print("=" * 72)


def export_summary(
    summary: BenchmarkSummary,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        json.dumps(
            asdict(
                summary
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def execute_measured_search(
    *,
    action: str,
    storage_name: str,
    dataset: str,
    records: int,
    storage: RecordStorage,
    args: argparse.Namespace,
    settings: SearchSettings,
) -> BenchmarkSummary:
    timed_storage = TimedRecordStorage(
        storage
    )
    query = build_query(
        dataset
    )

    for _ in range(
        args.warmups
    ):
        execute_pipeline(
            timed_storage=timed_storage,
            query=query,
            settings=settings,
            result_limit=args.result_limit,
        )

    metrics = [
        execute_pipeline(
            timed_storage=timed_storage,
            query=query,
            settings=settings,
            result_limit=args.result_limit,
        )
        for _ in range(
            args.repeats
        )
    ]

    return summarize(
        action=action,
        storage=storage_name,
        dataset=dataset,
        records=records,
        args=args,
        settings=settings,
        metrics=metrics,
    )


def search_prepared_dataset(
    args: argparse.Namespace,
    settings: SearchSettings,
) -> None:
    assert args.dataset is not None

    storage, record_count = open_prepared_dataset(
        args
    )

    try:
        summary = execute_measured_search(
            action="search",
            storage_name=args.storage,
            dataset=args.dataset,
            records=record_count,
            storage=storage,
            args=args,
            settings=settings,
        )

        print_summary(
            summary
        )

        if args.output is not None:
            export_summary(
                summary,
                args.output,
            )

            print(
                f"\nJSON result written to: "
                f"{args.output.resolve()}"
            )

    finally:
        storage.close()


def run_lifecycle(
    args: argparse.Namespace,
    settings: SearchSettings,
) -> None:
    dataset = (
        args.dataset
        or (
            "lifecycle-"
            + datetime.now(
                timezone.utc
            ).strftime(
                "%Y%m%dT%H%M%S%fZ"
            )
        )
    )

    lifecycle_started_at = time.perf_counter()

    generation_started_at = time.perf_counter()
    records = generate_records(
        args.records,
        seed=args.seed,
        dataset=dataset,
    )
    generation_ms = (
        time.perf_counter()
        - generation_started_at
    ) * 1_000

    storage: RecordStorage | None = None
    temporary_directory: (
        tempfile.TemporaryDirectory[str]
        | None
    ) = None
    preparation_ms = 0.0
    cleanup_ms = 0.0
    summary: BenchmarkSummary | None = None

    try:
        preparation_started_at = time.perf_counter()

        if args.storage == "json":
            temporary_directory = tempfile.TemporaryDirectory(
                prefix="hcp-search-benchmark-"
            )
            path = (
                Path(
                    temporary_directory.name
                )
                / "hcp_records.json"
            )

            write_json_dataset(
                path,
                records,
            )

            storage = JSONRecordStorage(
                file_path=path,
            )

        else:
            postgres_storage = open_postgres_storage(
                args
            )
            storage = postgres_storage

            existing = postgres_dataset_count(
                postgres_storage,
                dataset,
            )

            if existing:
                cleanup_postgres_dataset(
                    postgres_storage,
                    dataset,
                )

            insert_postgres_records(
                postgres_storage,
                records,
            )

        preparation_ms = (
            time.perf_counter()
            - preparation_started_at
        ) * 1_000

        base_summary = execute_measured_search(
            action="lifecycle",
            storage_name=args.storage,
            dataset=dataset,
            records=args.records,
            storage=storage,
            args=args,
            settings=settings,
        )

        cleanup_started_at = time.perf_counter()

        if (
            args.storage == "postgres"
            and isinstance(
                storage,
                PostgresRecordStorage,
            )
            and not args.keep_data
        ):
            cleanup_postgres_dataset(
                storage,
                dataset,
            )

        cleanup_ms = (
            time.perf_counter()
            - cleanup_started_at
        ) * 1_000

        summary = BenchmarkSummary(
            **{
                **asdict(
                    base_summary
                ),
                "candidate_search": base_summary.candidate_search,
                "candidate_evaluation": base_summary.candidate_evaluation,
                "correlation": base_summary.correlation,
                "case_builder": base_summary.case_builder,
                "total_pipeline": base_summary.total_pipeline,
                "dataset_generation_ms": generation_ms,
                "storage_preparation_ms": preparation_ms,
                "cleanup_ms": cleanup_ms,
                "lifecycle_total_ms": (
                    time.perf_counter()
                    - lifecycle_started_at
                )
                * 1_000,
            }
        )

        print_summary(
            summary
        )

        if args.output is not None:
            export_summary(
                summary,
                args.output,
            )

            print(
                f"\nJSON result written to: "
                f"{args.output.resolve()}"
            )

    finally:
        if (
            args.storage == "postgres"
            and isinstance(
                storage,
                PostgresRecordStorage,
            )
            and not args.keep_data
        ):
            remaining = postgres_dataset_count(
                storage,
                dataset,
            )

            if remaining:
                cleanup_postgres_dataset(
                    storage,
                    dataset,
                )

        if storage is not None:
            storage.close()

        if temporary_directory is not None:
            temporary_directory.cleanup()


def main() -> int:
    load_local_environment()

    args = parse_args()

    settings = SearchSettings(
        candidate_fetch_limit=(
            args.candidate_fetch_limit
        ),
        candidate_multiplier=(
            args.candidate_multiplier
        ),
        max_candidate_fetch_limit=(
            args.max_candidate_fetch_limit
        ),
    )

    if args.action == "prepare":
        prepare_dataset(
            args
        )
        return 0

    if args.action == "search":
        search_prepared_dataset(
            args,
            settings,
        )
        return 0

    if args.action == "cleanup":
        cleanup_dataset(
            args
        )
        return 0

    run_lifecycle(
        args,
        settings,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
