from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.errors import (
    InvalidStorageDataError,
    RecordAlreadyExistsError,
    StorageError,
)
from app.models.humanitarian_record import HumanitarianRecord
from app.storage.postgres_store import PostgresRecordStorage


DEFAULT_JSON_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "hcp_records.json"
)


@dataclass(frozen=True)
class ImportSummary:
    """
    Resultado resumido de una importación JSON → PostgreSQL.
    """

    source_path: Path
    total_records: int
    imported_records: int
    skipped_records: int

    @property
    def has_changes(self) -> bool:
        return self.imported_records > 0


def main() -> int:
    """
    Punto de entrada del comando:

        python -m app.tools.import_json
    """
    parser = build_argument_parser()
    arguments = parser.parse_args()

    try:
        summary = import_json_records(
            source_path=arguments.source,
            database_url=arguments.database_url,
            dry_run=arguments.dry_run,
        )

    except (
        InvalidStorageDataError,
        StorageError,
        ValueError,
    ) as exc:
        print(
            f"Import failed: {exc}",
            file=sys.stderr,
        )
        return 1

    print_import_summary(
        summary=summary,
        dry_run=arguments.dry_run,
    )

    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    """
    Construye los argumentos del comando.
    """
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.import_json",
        description=(
            "Import canonical Humanitarian Records from a JSON array "
            "into PostgreSQL."
        ),
    )

    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_JSON_PATH,
        help=(
            "Path to the JSON file. "
            f"Default: {DEFAULT_JSON_PATH}"
        ),
    )

    parser.add_argument(
        "--database-url",
        default=None,
        help=(
            "PostgreSQL URL. When omitted, DATABASE_URL is used. "
            "Avoid passing credentials directly in shared terminal history."
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate the JSON and calculate what would be imported "
            "without writing to PostgreSQL."
        ),
    )

    return parser


def import_json_records(
    source_path: Path,
    database_url: str | None = None,
    *,
    dry_run: bool = False,
) -> ImportSummary:
    """
    Importa registros canónicos desde JSON hacia PostgreSQL.

    La operación es idempotente:

    - los UUID inexistentes se importan;
    - los UUID ya almacenados se omiten;
    - ejecutar el comando varias veces no duplica registros.

    Args:
        source_path:
            Archivo JSON que contiene un arreglo de Humanitarian Records.

        database_url:
            URL PostgreSQL opcional. Si no se suministra, se utiliza
            DATABASE_URL.

        dry_run:
            Si es True, valida y calcula la importación sin escribir datos.

    Returns:
        Resumen de la operación.

    Raises:
        InvalidStorageDataError:
            Si el archivo no contiene JSON válido o algún registro no cumple
            el modelo HumanitarianRecord.

        StorageError:
            Si PostgreSQL no está disponible o la importación falla.
    """
    resolved_source_path = (
        source_path
        .expanduser()
        .resolve()
    )

    records = load_json_records(
        resolved_source_path
    )

    resolved_database_url = (
        database_url
        or os.getenv("DATABASE_URL")
    )

    if not resolved_database_url:
        raise ValueError(
            "DATABASE_URL is required to import records into PostgreSQL"
        )

    storage = PostgresRecordStorage(
        database_url=resolved_database_url,
    )

    try:
        storage.ping()

        new_records = [
            record
            for record in records
            if not storage.exists(
                record.id
            )
        ]

        skipped_count = (
            len(records)
            - len(new_records)
        )

        if dry_run:
            return ImportSummary(
                source_path=resolved_source_path,
                total_records=len(records),
                imported_records=len(new_records),
                skipped_records=skipped_count,
            )

        if new_records:
            try:
                storage.create_many(
                    new_records
                )

            except RecordAlreadyExistsError:
                """
                Puede ocurrir si otro proceso inserta un UUID entre la
                comprobación exists() y create_many().

                Reintentamos de forma segura registro por registro, omitiendo
                únicamente aquellos que ya existan.
                """
                imported_count = (
                    _create_missing_records_individually(
                        storage=storage,
                        records=new_records,
                    )
                )

                return ImportSummary(
                    source_path=resolved_source_path,
                    total_records=len(records),
                    imported_records=imported_count,
                    skipped_records=(
                        len(records)
                        - imported_count
                    ),
                )

        return ImportSummary(
            source_path=resolved_source_path,
            total_records=len(records),
            imported_records=len(new_records),
            skipped_records=skipped_count,
        )

    finally:
        storage.close()


def load_json_records(
    source_path: Path,
) -> list[HumanitarianRecord]:
    """
    Carga y valida completamente un archivo JSON de registros HCP.
    """
    if not source_path.exists():
        raise InvalidStorageDataError(
            f"JSON source file does not exist: {source_path}"
        )

    if not source_path.is_file():
        raise InvalidStorageDataError(
            f"JSON source path is not a file: {source_path}"
        )

    try:
        raw_content = source_path.read_text(
            encoding="utf-8"
        )

    except OSError as exc:
        raise InvalidStorageDataError(
            f"Unable to read JSON source file: {source_path}"
        ) from exc

    try:
        raw_data: Any = json.loads(
            raw_content
        )

    except json.JSONDecodeError as exc:
        raise InvalidStorageDataError(
            f"JSON source file is invalid: {source_path}"
        ) from exc

    if not isinstance(
        raw_data,
        list,
    ):
        raise InvalidStorageDataError(
            "JSON source root must be an array of Humanitarian Records"
        )

    records: list[
        HumanitarianRecord
    ] = []

    seen_ids = set()

    for index, item in enumerate(
        raw_data
    ):
        if not isinstance(
            item,
            dict,
        ):
            raise InvalidStorageDataError(
                f"JSON item at index {index} must be an object"
            )

        try:
            record = (
                HumanitarianRecord
                .model_validate(item)
            )

        except ValidationError as exc:
            raise InvalidStorageDataError(
                f"Invalid Humanitarian Record at JSON index {index}"
            ) from exc

        if record.id in seen_ids:
            raise InvalidStorageDataError(
                "JSON source contains duplicate Humanitarian Record ID "
                f"{record.id}"
            )

        seen_ids.add(
            record.id
        )

        records.append(
            record
        )

    return records


def _create_missing_records_individually(
    storage: PostgresRecordStorage,
    records: list[HumanitarianRecord],
) -> int:
    """
    Importa individualmente después de un conflicto concurrente de lote.
    """
    imported_count = 0

    for record in records:
        if storage.exists(
            record.id
        ):
            continue

        try:
            storage.create(
                record
            )

        except RecordAlreadyExistsError:
            continue

        imported_count += 1

    return imported_count


def print_import_summary(
    summary: ImportSummary,
    *,
    dry_run: bool,
) -> None:
    """
    Muestra un resumen sin exponer credenciales ni contenido sensible.
    """
    mode_label = (
        "DRY RUN"
        if dry_run
        else "IMPORT COMPLETE"
    )

    print()
    print(mode_label)
    print("-" * len(mode_label))
    print(
        f"Source: {summary.source_path}"
    )
    print(
        f"Total records: {summary.total_records}"
    )
    print(
        f"Imported records: {summary.imported_records}"
    )
    print(
        f"Skipped existing records: {summary.skipped_records}"
    )

    if dry_run:
        print(
            "No data was written to PostgreSQL."
        )
    elif summary.has_changes:
        print(
            "PostgreSQL now contains the newly imported records."
        )
    else:
        print(
            "No new records were imported."
        )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
