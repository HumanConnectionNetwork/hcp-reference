from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.config import API_PREFIX, RECORDS_FILE
from app.core.errors import (
    CorrelationProcessingError,
    InvalidStorageDataError,
    QueryProcessingError,
    StorageError,
)
from app.models.correlation import CorrelationResult
from app.models.humanitarian_case import HumanitarianCase
from app.models.query import HumanitarianQuery
from app.services.correlation import CorrelationService
from app.services.search import SearchService
from app.storage.json_store import JSONRecordStorage


router = APIRouter(
    prefix=f"{API_PREFIX}/correlation",
    tags=["Correlation"],
)

storage = JSONRecordStorage(RECORDS_FILE)
search_service = SearchService(storage)
correlation_service = CorrelationService()


class CorrelationResponse(BaseModel):
    """
    Response returned by the local correlation endpoint.
    """

    query: HumanitarianQuery

    candidate_count: Annotated[
        int,
        Field(
            ge=0,
            description=(
                "Number of records selected for local correlation."
            ),
        ),
    ]

    result_count: Annotated[
        int,
        Field(
            ge=0,
            description=(
                "Number of correlation results returned after filtering."
            ),
        ),
    ]

    results: list[CorrelationResult]

    humanitarian_case: HumanitarianCase | None = None


@router.post(
    "",
    response_model=CorrelationResponse,
    response_model_exclude_none=True,
    summary="Correlate Humanitarian Records",
    description=(
        "Search locally stored Humanitarian Records and calculate "
        "explainable correlation results using a structured HCP Query."
    ),
)
def correlate_humanitarian_records(
    humanitarian_query: HumanitarianQuery,
    limit: Annotated[
        int | None,
        Query(
            ge=1,
            le=100,
            description=(
                "Optional maximum number of correlation results to return."
            ),
        ),
    ] = None,
    minimum_score: Annotated[
        float,
        Query(
            ge=0.0,
            le=100.0,
            description=(
                "Minimum correlation score required for a result."
            ),
        ),
    ] = 0.0,
    generate_case: Annotated[
        bool,
        Query(
            description=(
                "Generate a local Humanitarian Case when results exist."
            ),
        ),
    ] = True,
) -> CorrelationResponse:
    """
    Search and correlate locally stored Humanitarian Records.

    The returned scores express compatibility between humanitarian
    observations. They do not confirm the identity of a human or animal.
    """
    try:
        candidates = search_service.search_records(
            query=humanitarian_query,
        )

        results = correlation_service.correlate_records(
            query=humanitarian_query,
            records=candidates,
            limit=limit,
            minimum_score=minimum_score,
        )

        humanitarian_case = None

        if generate_case and results:
            humanitarian_case = correlation_service.generate_case(
                query=humanitarian_query,
                results=results,
            )

        return CorrelationResponse(
            query=humanitarian_query,
            candidate_count=len(candidates),
            result_count=len(results),
            results=results,
            humanitarian_case=humanitarian_case,
        )

    except QueryProcessingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "query_processing_error",
                "message": str(exc),
            },
        ) from exc

    except CorrelationProcessingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "correlation_processing_error",
                "message": str(exc),
            },
        ) from exc

    except InvalidStorageDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "invalid_storage_data",
                "message": str(exc),
            },
        ) from exc

    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "storage_error",
                "message": str(exc),
            },
        ) from exc

