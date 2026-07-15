```python
from fastapi import FastAPI
from pydantic import BaseModel

from app.api.correlation import router as correlation_router
from app.api.health import router as health_router
from app.api.records import router as records_router
from app.api.search import router as search_router
from app.core.config import (
    APP_DESCRIPTION,
    APP_NAME,
    APP_VERSION,
    RECORDS_FILE,
    ensure_data_directory,
)
from app.storage.json_store import JSONRecordStorage


class RootResponse(BaseModel):
    """
    Informational response returned by the application root endpoint.
    """

    service: str
    version: str
    description: str
    documentation: str
    health: str


def create_application() -> FastAPI:
    """
    Create and configure the HCP Reference Node application.

    Application construction remains explicit so tests and future execution
    environments can create isolated FastAPI instances when needed.
    """
    ensure_data_directory()

    storage = JSONRecordStorage(RECORDS_FILE)
    storage.list_all()

    application = FastAPI(
        title=APP_NAME,
        version=APP_VERSION,
        description=APP_DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    application.include_router(health_router)
    application.include_router(records_router)
    application.include_router(search_router)
    application.include_router(correlation_router)

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


app = create_application()
```
