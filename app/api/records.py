python
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.core.config import API_PREFIX, RECORDS_FILE
from app.core.errors import (
    InvalidStorageDataError,
    RecordAlreadyExistsError,
    RecordNotFoundError,
    StorageError,
)
from app.models.humanitarian_record import HumanitarianRecord
from app.services.records import RecordService
from app.storage.json_store import JSONRecordStorage


router = APIRouter(
    prefix=f"{API_PREFIX}/records",
    tags=["Humanitarian Records"],
)

storage = JSONRecordStorage(RECORDS_FILE)
record_service = RecordService(storage)


@router.post(
    "",
    response_model=HumanitarianRecord,
    status_code=status.HTTP_201_CREATED,
    summary="Create a Humanitarian Record",
    description=(
        "Validate and store one canonical Humanitarian Record in the local "
        "HCP Reference Node."
    ),
)
def create_humanitarian_record(
    record: HumanitarianRecord,
) -> HumanitarianRecord:
    """
    Create and persist one canonical Humanitarian Record.

    The client supplies the record identifier. The reference node validates
    the complete record and rejects duplicate identifiers.
    """
    try:
        return record_service.create_record(record)

    except RecordAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "record_already_exists",
                "message": str(exc),
                "record_id": exc.record_id,
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


@router.get(
    "/{record_id}",
    response_model=HumanitarianRecord,
    summary="Get a Humanitarian Record by ID",
    description=(
        "Retrieve one locally stored Humanitarian Record using its UUID."
    ),
)
def get_humanitarian_record(
    record_id: UUID,
) -> HumanitarianRecord:
    """
    Retrieve one Humanitarian Record by its record identifier.

    The identifier refers only to the Humanitarian Record. It does not
    identify the human or animal described by the observation.
    """
    try:
        return record_service.get_record(record_id)

    except RecordNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "record_not_found",
                "message": str(exc),
                "record_id": exc.record_id,
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


