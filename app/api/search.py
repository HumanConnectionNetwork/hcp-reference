from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.config import API_PREFIX, RECORDS_FILE
from app.core.errors import (
    InvalidStorageDataError,
    QueryProcessingError,
    StorageError,
)
from app.models.humanitarian_record import HumanitarianRecord
from app.models.query import HumanitarianQuery
from app.services.search import SearchService
from app.storage.json_store import JSONRecordStorage


router = APIRouter(
    prefix=f"{API_PREFIX}/search",
    tags=["Search"],
)

storage = JSONRecordStorage(RECORDS_FILE)
search_service = SearchService(storage)


class SearchResponse(BaseModel):
    """
    Response returned by the local Humanitarian Record search endpoint.
    """

    query: HumanitarianQuery

    count: Annotated[
        int,
        Field(
            ge=0,
            description="Number of candidate records returned.",
        ),
    ]

    records: list[HumanitarianRecord]


@router.post(
    "",
    response_model=SearchResponse,
    response_model_exclude_none=True,
    summary="Search Humanitarian Records",
    description=(
        "Search locally stored Humanitarian Records using a structured HCP "
        "Query. The response contains broad candidates for later correlation."
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
                "Optional maximum number of candidate records to return."
            ),
        ),
    ] = None,
) -> SearchResponse:
    """
    Search for locally stored records compatible with partial humanitarian
    evidence.

    Search does not calculate correlation scores and does not confirm identity.
    Results are ordered from the most recently observed record to the oldest.
    """
    try:
        records = search_service.search_records(
            query=humanitarian_query,
            limit=limit,
        )

        return SearchResponse(
            query=humanitarian_query,
            count=len(records),
            records=records,
        )

    except QueryProcessingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "query_processing_error",
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
```

