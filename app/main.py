from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from pydantic import BaseModel

from app.api.health import router as health_router
from app.api.records import router as records_router
from app.api.search import router as search_router
from app.core.config import (
    APP_DESCRIPTION,
    APP_NAME,
    APP_VERSION,
)
from app.storage.base import RecordStorage
from app.storage.factory import create_record_storage


class RootResponse(BaseModel):
    """
    Informational response returned by the application root endpoint.
    """

    service: str
    version: str
    description: str
    documentation: str
    health: str


@asynccontextmanager
async def application_lifespan(
    application: FastAPI,
) -> AsyncIterator[None]:
    """
    Initialize and release application infrastructure.

    Storage is selected through the configured RecordStorage factory:

        HCP_STORAGE=json
        HCP_STORAGE=postgres

    The resulting implementation is stored in application.state so every
    endpoint and service can use the same storage instance.
    """
    storage = create_record_storage()

    try:
        _validate_storage_connection(
            storage
        )

        application.state.record_storage = (
            storage
        )

        yield

    finally:
        storage.close()


def create_application() -> FastAPI:
    """
    Create and configure the HCP Reference Node application.

    Application construction remains explicit so tests and future execution
    environments can create isolated FastAPI instances when needed.
    """
    application = FastAPI(
        title=APP_NAME,
        version=APP_VERSION,
        description=APP_DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=application_lifespan,
    )

    application.include_router(
        health_router
    )

    application.include_router(
        records_router
    )

    application.include_router(
        search_router
    )

    @application.get(
        "/",
        response_model=RootResponse,
        tags=["Application"],
        summary="Get reference node information",
        description=(
            "Return basic information about the HCP Reference Node and its "
            "available documentation."
        ),
    )
    def get_root_information() -> RootResponse:
        """
        Return application metadata without executing protocol operations.
        """
        return RootResponse(
            service=APP_NAME,
            version=APP_VERSION,
            description=APP_DESCRIPTION,
            documentation="/docs",
            health="/health",
        )

    return application


def get_application_storage(
    request: Request,
) -> RecordStorage:
    """
    Return the RecordStorage instance owned by the current application.

    API modules may import this helper later through a dependency module.
    Keeping storage in application.state ensures that all requests share the
    same PostgreSQL Engine and connection pool.
    """
    storage = getattr(
        request.app.state,
        "record_storage",
        None,
    )

    if not isinstance(
        storage,
        RecordStorage,
    ):
        raise RuntimeError(
            "RecordStorage has not been initialized"
        )

    return storage


def _validate_storage_connection(
    storage: RecordStorage,
) -> None:
    """
    Fail application startup when the configured storage is unavailable.

    JSON storage is validated by reading and validating its records.

    PostgreSQL storage exposes ping(), allowing startup to verify the database
    without loading every persisted Humanitarian Record.
    """
    ping = getattr(
        storage,
        "ping",
        None,
    )

    if callable(ping):
        ping()
        return

    storage.list_all()


app = create_application()
