from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import APP_NAME, APP_VERSION


router = APIRouter(
    tags=["Health"],
)


class HealthResponse(BaseModel):
    """
    Minimal health response for the HCP Reference Node.
    """

    status: Literal["ok"]
    service: str
    version: str


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Check node health",
    description=(
        "Confirm that the HCP Reference Node application is running."
    ),
)
def health_check() -> HealthResponse:
    """
    Return the current application health status.

    This endpoint verifies application availability only. It does not perform
    storage diagnostics, federation checks or production monitoring.
    """
    return HealthResponse(
        status="ok",
        service=APP_NAME,
        version=APP_VERSION,
    )

