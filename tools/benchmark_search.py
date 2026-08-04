#!/usr/bin/env python3
"""
Official HCP search benchmark.

The tool generates reproducible Humanitarian Records and supports two modes:

- lifecycle: prepare data, execute the search pipeline and clean up;
- search-only: prepare data once and report only repeated search-pipeline timing.

It reports timing for:

- candidate retrieval;
- semantic candidate evaluation;
- correlation;
- Humanitarian Case construction;
- total pipeline time.

Examples
--------

JSON benchmark using a temporary file:

    python tools/benchmark_search.py --storage json --records 10000

PostgreSQL benchmark using a disposable or development database:

    python tools/benchmark_search.py \
        --storage postgres \
        --records 10000 \
        --database-url "postgresql+psycopg://..."

Export results:

    python tools/benchmark_search.py \
        --storage json \
        --records 10000 \
        --output benchmark_results/search_10000.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5, NAMESPACE_URL


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )


from sqlalchemy import delete  # noqa: E402

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
DEFAULT_RECORD_COUNTS = (100, 1_000, 10_000, 100_000)


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
class BenchmarkSummary:
    mode: str
    storage: str
    records: int
    repeats: int
    warmups: int
    candidate_fetch_limit: int
    candidate_multiplier: int
    max_candidate_fetch_limit: int
    result_limit: int
    candidate_search_ms: float
    candidate_evaluation_ms: float
    correlation_ms: float
    case_builder_ms: float
    total_ms: float
    candidates_fetched: int
    search_results: int
    correlation_results: int
    case_built: bool
    dataset_generation_ms: float | None
    storage_preparation_ms: float | None
    cleanup_ms: float | None
    lifecycle_total_ms: float | None
    generated_at: str


class TimedRecordStorage(RecordStorage):
    """
    Transparent RecordStorage decorator that measures candidate retrieval.

    SearchService still receives a RecordStorage abstraction and executes its
    normal behavior. The decorator records only the time spent inside
    search_candidates(), allowing semantic evaluation time to be derived from
    the complete SearchService duration.
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

    Existing environment variables always take precedence. This keeps the
    benchmark aligned with the backend without adding a dotenv dependency.
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
            "Generate reproducible HCP records and benchmark the complete "
            "candidate-search, correlation and case-building pipeline."
        )
    )

    parser.add_argument(
        "--mode",
        choices=("lifecycle", "search-only"),
        default="lifecycle",
        help=(
            "Benchmark mode. lifecycle preserves the complete automatic "
            "generate/load/search/cleanup workflow. search-only prepares the "
            "dataset once and reports only repeated search-pipeline timing. "
            "Default: lifecycle."
        ),
    )
    parser.add_argument(
        "--storage",
        choices=("json", "postgres"),
        default="json",
        help="Storage backend used by the benchmark.",
    )
    parser.add_argument(
        "--records",
        type=int,
        default=10_000,
        help="Number of generated records. Default: 10000.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=5,
        help="Measured pipeline executions. Default: 5.",
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
            "PostgreSQL URL. Required for --storage postgres unless "
            "DATABASE_URL is defined."
        ),
    )
    parser.add_argument(
        "--initialize-schema",
        action="store_true",
        help=(
            "Create the humanitarian_records table when using PostgreSQL. "
            "Use only with a disposable or development database."
        ),
    )
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help=(
            "Keep generated PostgreSQL benchmark records after execution. "
            "By default they are removed."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON output path.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260804,
        help="Deterministic dataset seed. Default: 20260804.",
    )

    args = parser.parse_args()

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
        parser.error("--warmups must be greater than or equal to 0")

    if (
        args.max_candidate_fetch_limit
        < args.candidate_fetch_limit
    ):
        parser.error(
            "--max-candidate-fetch-limit must be greater than or equal to "
            "--candidate-fetch-limit"
        )

    return args


def build_query() -> HumanitarianQuery:
    return HumanitarianQuery.model_validate(
        {
            "query_id": "2b2e1874-9fa1-47a5-af83-947013b1de44",
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
                    "country_code": "VE",
                    "admin_level_1": "Distrito Capital",
                    "admin_level_2": "Libertador",
                    "locality": "Caracas",
                    "district": "La Candelaria",
                },
                "searched_at": "2026-08-04T12:00:00Z",
            },
        }
    )


def generate_records(
    count: int,
    *,
    seed: int,
    run_id: str,
) -> list[HumanitarianRecord]:
    """
    Generate deterministic schema 0.6 records.

    A controlled minority is geographically and descriptively compatible with
    the benchmark query. The remaining records exercise storage filtering and
    semantic rejection.
    """
    records: list[HumanitarianRecord] = []
    base_time = datetime(
        2026,
        8,
        4,
        11,
        0,
        tzinfo=timezone.utc,
    )

    alternate_locations = (
        ("VE", "Zulia", "Maracaibo"),
        ("VE", "Carabobo", "Valencia"),
        ("BR", "Rio de Janeiro", "Rio das Ostras"),
        ("CO", "Distrito Capital", "Bogotá"),
    )

    for index in range(count):
        matching_location = index % 20 == 0
        matching_description = index % 100 == 0

        if matching_location:
            country_code = "VE"
            admin_level_1 = "Distrito Capital"
            locality = "Caracas"
        else:
            (
                country_code,
                admin_level_1,
                locality,
            ) = alternate_locations[
                (index + seed)
                % len(alternate_locations)
            ]

        if matching_description:
            reported_label = "María González"
            estimated_age = 34
            recognition_features = (
                "cabello negro chaqueta azul cicatriz ceja derecha"
            )
        else:
            reported_label = f"Persona Benchmark {index:08d}"
            estimated_age = 18 + ((index + seed) % 70)
            recognition_features = (
                f"descripcion sintetica benchmark numero {index}"
            )

        record_id = uuid5(
            NAMESPACE_URL,
            f"{run_id}:{seed}:{index}",
        )

        records.append(
            HumanitarianRecord.model_validate(
                {
                    "id": str(record_id),
                    "schema_version": "0.6",
                    "source_client": run_id,
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
                            - timedelta(minutes=index)
                        ).isoformat(),
                        "declared_location": {
                            "country_code": country_code,
                            "admin_level_1": admin_level_1,
                            "admin_level_2": (
                                "Libertador"
                                if matching_location
                                else "Benchmark"
                            ),
                            "locality": locality,
                            "district": (
                                "La Candelaria"
                                if matching_location
                                else None
                            ),
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
    path.write_text(
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


def create_storage(
    args: argparse.Namespace,
    records: list[HumanitarianRecord],
) -> tuple[RecordStorage, tempfile.TemporaryDirectory[str] | None]:
    if args.storage == "json":
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="hcp-search-benchmark-"
        )
        storage_path = (
            Path(temporary_directory.name)
            / "hcp_records.json"
        )

        write_json_dataset(
            storage_path,
            records,
        )

        return (
            JSONRecordStorage(
                file_path=storage_path,
            ),
            temporary_directory,
        )

    database_url = (
        args.database_url
        or os.getenv("DATABASE_URL")
    )

    if not database_url:
        raise SystemExit(
            "PostgreSQL benchmark requires --database-url or DATABASE_URL."
        )

    storage = PostgresRecordStorage(
        database_url=database_url,
        application_name="hcp-reference-benchmark",
    )

    if args.initialize_schema:
        metadata.create_all(
            storage.engine
        )

    storage.ping()

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

    return storage, None


def cleanup_postgres_records(
    storage: PostgresRecordStorage,
    source_client: str,
) -> None:
    statement = delete(
        humanitarian_records_table
    ).where(
        humanitarian_records_table.c.source_client
        == source_client
    )

    with storage.engine.begin() as connection:
        connection.execute(statement)


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
        search_results=len(records),
        correlation_results=len(correlations),
        case_built=case_built,
    )


def median_metric(
    metrics: list[IterationMetrics],
    field_name: str,
) -> float:
    return float(
        statistics.median(
            getattr(metric, field_name)
            for metric in metrics
        )
    )


def summarize(
    *,
    args: argparse.Namespace,
    settings: SearchSettings,
    metrics: list[IterationMetrics],
) -> BenchmarkSummary:
    representative = metrics[
        len(metrics)
        // 2
    ]

    return BenchmarkSummary(
        mode=args.mode,
        storage=args.storage,
        records=args.records,
        repeats=args.repeats,
        warmups=args.warmups,
        candidate_fetch_limit=(
            settings.candidate_fetch_limit
        ),
        candidate_multiplier=(
            settings.candidate_multiplier
        ),
        max_candidate_fetch_limit=(
            settings.max_candidate_fetch_limit
        ),
        result_limit=args.result_limit,
        candidate_search_ms=median_metric(
            metrics,
            "candidate_search_ms",
        ),
        candidate_evaluation_ms=median_metric(
            metrics,
            "candidate_evaluation_ms",
        ),
        correlation_ms=median_metric(
            metrics,
            "correlation_ms",
        ),
        case_builder_ms=median_metric(
            metrics,
            "case_builder_ms",
        ),
        total_ms=median_metric(
            metrics,
            "total_ms",
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
        dataset_generation_ms=None,
        storage_preparation_ms=None,
        cleanup_ms=None,
        lifecycle_total_ms=None,
        generated_at=datetime.now(
            timezone.utc
        ).isoformat(),
    )


def print_summary(
    summary: BenchmarkSummary,
) -> None:
    print()
    print("HCP Search Benchmark")
    print("=" * 56)
    print(f"Mode                     : {summary.mode}")
    print(f"Storage                  : {summary.storage}")
    print(f"Generated records        : {summary.records:,}")
    print(f"Measured repetitions     : {summary.repeats}")
    print(f"Warm-up repetitions      : {summary.warmups}")
    print(f"Final result limit       : {summary.result_limit}")
    print(f"Candidates fetched       : {summary.candidates_fetched}")
    print(f"Search results           : {summary.search_results}")
    print(f"Correlation results      : {summary.correlation_results}")
    print(f"Humanitarian Case built  : {summary.case_built}")
    print("-" * 56)
    print(
        f"Candidate search median  : "
        f"{summary.candidate_search_ms:10.3f} ms"
    )
    print(
        f"Candidate evaluation     : "
        f"{summary.candidate_evaluation_ms:10.3f} ms"
    )
    print(
        f"Correlation median       : "
        f"{summary.correlation_ms:10.3f} ms"
    )
    print(
        f"Case builder median      : "
        f"{summary.case_builder_ms:10.3f} ms"
    )
    print(
        f"Total pipeline median    : "
        f"{summary.total_ms:10.3f} ms"
    )

    if summary.mode == "lifecycle":
        print("-" * 56)

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

    print("=" * 56)


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
            asdict(summary),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


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

    run_id = (
        f"{BENCHMARK_SOURCE_PREFIX}:"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
    )

    lifecycle_started_at = time.perf_counter()

    print(
        f"Generating {args.records:,} canonical HCP records..."
    )

    generation_started_at = time.perf_counter()

    records = generate_records(
        args.records,
        seed=args.seed,
        run_id=run_id,
    )

    dataset_generation_ms = (
        time.perf_counter()
        - generation_started_at
    ) * 1_000

    storage: RecordStorage | None = None
    temporary_directory: (
        tempfile.TemporaryDirectory[str]
        | None
    ) = None
    summary: BenchmarkSummary | None = None
    cleanup_ms: float | None = None

    try:
        preparation_started_at = time.perf_counter()

        storage, temporary_directory = create_storage(
            args,
            records,
        )

        storage_preparation_ms = (
            time.perf_counter()
            - preparation_started_at
        ) * 1_000

        timed_storage = TimedRecordStorage(
            storage
        )
        query = build_query()

        for _ in range(args.warmups):
            execute_pipeline(
                timed_storage=timed_storage,
                query=query,
                settings=settings,
                result_limit=args.result_limit,
            )

        measured_metrics = [
            execute_pipeline(
                timed_storage=timed_storage,
                query=query,
                settings=settings,
                result_limit=args.result_limit,
            )
            for _ in range(args.repeats)
        ]

        summary = summarize(
            args=args,
            settings=settings,
            metrics=measured_metrics,
        )

        if args.mode == "search-only":
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

        return 0

    finally:
        cleanup_started_at = time.perf_counter()

        if (
            args.storage == "postgres"
            and isinstance(
                storage,
                PostgresRecordStorage,
            )
            and not args.keep_data
        ):
            try:
                cleanup_postgres_records(
                    storage,
                    run_id,
                )
            except Exception as exc:
                print(
                    "WARNING: benchmark records could not be removed "
                    f"automatically: {exc}",
                    file=sys.stderr,
                )

        if storage is not None:
            storage.close()

        if temporary_directory is not None:
            temporary_directory.cleanup()

        cleanup_ms = (
            time.perf_counter()
            - cleanup_started_at
        ) * 1_000

        if (
            args.mode == "lifecycle"
            and summary is not None
        ):
            lifecycle_total_ms = (
                time.perf_counter()
                - lifecycle_started_at
            ) * 1_000

            lifecycle_summary = replace(
                summary,
                dataset_generation_ms=dataset_generation_ms,
                storage_preparation_ms=storage_preparation_ms,
                cleanup_ms=cleanup_ms,
                lifecycle_total_ms=lifecycle_total_ms,
            )

            print_summary(
                lifecycle_summary
            )

            if args.output is not None:
                export_summary(
                    lifecycle_summary,
                    args.output,
                )

                print(
                    f"\nJSON result written to: "
                    f"{args.output.resolve()}"
                )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
