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
from app.models.humanitarian_case import HumanitarianCase
from app.models.query import HumanitarianQuery
from app.services.case_builder import HumanitarianCaseBuilder
from app.services.correlation import CorrelationService
from app.services.search import SearchService
from app.storage.json_store import JSONRecordStorage


router = APIRouter(
    prefix=f"{API_PREFIX}/search",
    tags=["Search"],
)

storage = JSONRecordStorage(RECORDS_FILE)

search_service = SearchService(storage)
correlation_service = CorrelationService()
case_builder = HumanitarianCaseBuilder()


class SearchResponse(BaseModel):
    """
    Response returned by the local humanitarian search endpoint.

    A search may produce no Humanitarian Case when no compatible records meet
    the requested minimum correlation score.
    """

    query: HumanitarianQuery

    candidate_count: Annotated[
        int,
        Field(
            ge=0,
            description=(
                "Number of broad candidate records found before correlation."
            ),
        ),
    ]

    correlated_count: Annotated[
        int,
        Field(
            ge=0,
            description=(
                "Number of correlation results included in the local case."
            ),
        ),
    ]

    humanitarian_case: HumanitarianCase | None = None


@router.post(
    "",
    response_model=SearchResponse,
    response_model_exclude_none=True,
    summary="Search and correlate Humanitarian Records",
    description=(
        "Search locally stored Humanitarian Records using a structured HCP "
        "Query, calculate explainable correlation results and build a local "
        "Humanitarian Case when compatible evidence is found. The resulting "
        "case is probabilistic and does not confirm identity."
    ),
)
def search_humanitarian_records(
    humanitarian_query: HumanitarianQuery,
    limit: Annotated[
        int | None,
        Query(
            ge=1,
            le=100,
            description=(
                "Optional maximum number of correlated records to include "
                "in the Humanitarian Case."
            ),
        ),
    ] = None,
    minimum_score: Annotated[
        float,
        Query(
            ge=0.0,
            le=100.0,
            description=(
                "Minimum correlation score required for a candidate record "
                "to be included in the Humanitarian Case."
            ),
        ),
    ] = 0.0,
) -> SearchResponse:
    """
    Search, correlate and interpret local Humanitarian Records.

    Processing sequence:

    1. SearchService selects broad candidates.
    2. CorrelationService evaluates compatibility with the Query.
    3. HumanitarianCaseBuilder creates a local explainable interpretation.

    The returned Humanitarian Case does not establish identity and always
    requires human verification.
    """
    try:
        candidate_records = search_service.search_records(
            query=humanitarian_query,
            limit=None,
        )

        if not candidate_records:
            return SearchResponse(
                query=humanitarian_query,
                candidate_count=0,
                correlated_count=0,
                humanitarian_case=None,
            )

        correlation_results = correlation_service.correlate_records(
            query=humanitarian_query,
            records=candidate_records,
            limit=limit,
            minimum_score=minimum_score,
        )

        if not correlation_results:
            return SearchResponse(
                query=humanitarian_query,
                candidate_count=len(candidate_records),
                correlated_count=0,
                humanitarian_case=None,
            )

        correlated_record_ids = {
            result.record_id
            for result in correlation_results
        }

        correlated_records = [
            record
            for record in candidate_records
            if record.id in correlated_record_ids
        ]

        humanitarian_case = case_builder.build(
            query=humanitarian_query,
            results=correlation_results,
            records=correlated_records,
        )

        return SearchResponse(
            query=humanitarian_query,
            candidate_count=len(candidate_records),
            correlated_count=len(correlation_results),
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
