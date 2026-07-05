from datetime import datetime, timezone
from uuid import uuid4, UUID
from typing import List

from app.schemas.humanitarian_record import (
    HumanitarianRecordCreate,
    HumanitarianRecord,
)
from app.hcp.validation.validator import validate_humanitarian_record_payload
from app.storage.memory import record_store


def create_humanitarian_record(
    payload: HumanitarianRecordCreate,
) -> HumanitarianRecord:
    validate_humanitarian_record_payload(payload)

    record = HumanitarianRecord(
        record_uuid=uuid4(),
        created_at=datetime.now(timezone.utc),
        event_classification=payload.event_classification,
        observation=payload.observation,
        source=payload.source,
        metadata=payload.metadata,
    )

    return record_store.save(record)


def get_humanitarian_record(record_uuid: UUID) -> HumanitarianRecord | None:
    return record_store.get(record_uuid)


def list_humanitarian_records() -> List[HumanitarianRecord]:
    return record_store.list_all()
