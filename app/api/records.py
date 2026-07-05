from uuid import UUID
from fastapi import APIRouter, HTTPException

from app.schemas.humanitarian_record import (
    HumanitarianRecordCreate,
    HumanitarianRecord,
)
from app.hcp.records.service import (
    create_humanitarian_record,
    get_humanitarian_record,
    list_humanitarian_records,
)

router = APIRouter(prefix="/records", tags=["records"])


@router.post("", response_model=HumanitarianRecord, status_code=201)
def create_record(payload: HumanitarianRecordCreate):
    try:
        return create_humanitarian_record(payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.get("/{record_uuid}", response_model=HumanitarianRecord)
def get_record(record_uuid: UUID):
    record = get_humanitarian_record(record_uuid)

    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")

    return record


@router.get("", response_model=list[HumanitarianRecord])
def list_records():
    return list_humanitarian_records()
