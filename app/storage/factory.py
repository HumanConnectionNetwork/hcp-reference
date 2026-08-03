import os
from pathlib import Path

from app.core.errors import StorageError
from app.storage.base import RecordStorage
from app.storage.json_store import JSONRecordStorage
from app.storage.postgres_store import PostgresRecordStorage


DEFAULT_STORAGE_BACKEND = "json"
SUPPORTED_STORAGE_BACKENDS = {
    "json",
    "postgres",
    "postgresql",
}

DEFAULT_JSON_STORAGE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "hcp_records.json"
)


def create_record_storage() -> RecordStorage:
    """
    Crea la implementación de RecordStorage configurada para el nodo HCP.

    Variables de entorno admitidas:

        HCP_STORAGE

            json
            postgres
            postgresql

        HCP_JSON_STORAGE_PATH

            Ruta opcional del archivo JSON cuando HCP_STORAGE=json.

        DATABASE_URL

            Cadena de conexión PostgreSQL cuando HCP_STORAGE=postgres.

    Ejemplos:

        HCP_STORAGE=json

        HCP_STORAGE=postgres
        DATABASE_URL=postgresql+psycopg://...

    La lógica de aplicación depende únicamente de RecordStorage y no necesita
    conocer qué tecnología de persistencia fue seleccionada.
    """
    storage_backend = _get_storage_backend()

    if storage_backend == "json":
        return _create_json_storage()

    if storage_backend in {
        "postgres",
        "postgresql",
    }:
        return _create_postgres_storage()

    raise StorageError(
        f"Unsupported HCP storage backend: {storage_backend}"
    )


def _create_json_storage() -> JSONRecordStorage:
    """
    Crea el almacenamiento JSON local.

    Si HCP_JSON_STORAGE_PATH no está definido, utiliza:

        data/hcp_records.json
    """
    configured_path = os.getenv(
        "HCP_JSON_STORAGE_PATH"
    )

    if configured_path:
        file_path = Path(
            configured_path
        ).expanduser()

        if not file_path.is_absolute():
            file_path = (
                Path.cwd()
                / file_path
            )

        file_path = (
            file_path.resolve()
        )
    else:
        file_path = (
            DEFAULT_JSON_STORAGE_PATH
        )

    return JSONRecordStorage(
        file_path=file_path
    )


def _create_postgres_storage() -> PostgresRecordStorage:
    """
    Crea el almacenamiento PostgreSQL.

    DATABASE_URL es obligatoria y debe apuntar al PostgreSQL utilizado por
    hcp-reference.

    El valor puede comenzar con:

        postgres://
        postgresql://
        postgresql+psycopg://

    PostgresRecordStorage normaliza automáticamente el driver para Psycopg 3.
    """
    database_url = _get_required_environment_variable(
        "DATABASE_URL"
    )

    return PostgresRecordStorage(
        database_url=database_url,
    )


def _get_storage_backend() -> str:
    """
    Obtiene y normaliza el backend de almacenamiento configurado.
    """
    configured_backend = os.getenv(
        "HCP_STORAGE",
        DEFAULT_STORAGE_BACKEND,
    )

    normalized_backend = (
        configured_backend
        .strip()
        .casefold()
    )

    if not normalized_backend:
        normalized_backend = (
            DEFAULT_STORAGE_BACKEND
        )

    if (
        normalized_backend
        not in SUPPORTED_STORAGE_BACKENDS
    ):
        supported_values = ", ".join(
            sorted(
                SUPPORTED_STORAGE_BACKENDS
            )
        )

        raise StorageError(
            "Invalid HCP_STORAGE value "
            f"'{configured_backend}'. "
            f"Supported values: {supported_values}"
        )

    return normalized_backend


def _get_required_environment_variable(
    name: str,
) -> str:
    """
    Lee una variable obligatoria sin exponer su contenido en el error.
    """
    value = os.getenv(name)

    if value is None:
        raise StorageError(
            f"{name} is required for the selected storage backend"
        )

    normalized_value = value.strip()

    if not normalized_value:
        raise StorageError(
            f"{name} cannot be empty for the selected storage backend"
        )

    return normalized_value
