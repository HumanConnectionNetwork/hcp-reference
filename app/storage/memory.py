from typing import Dict, Optional, List
from uuid import UUID
from app.schemas.humanitarian_record import HumanitarianRecord


class InMemoryRecordStore:
    def __init__(self):
        self.records: Dict[UUID, HumanitarianRecord] = {}

    def save(self, record: HumanitarianRecord) -> HumanitarianRecord:
        if record.record_uuid in self.records:
            raise ValueError("Record already exists")
        self.records[record.record_uuid] = record
        return record

    def get(self, record_uuid: UUID) -> Optional[HumanitarianRecord]:
        return self.records.get(record_uuid)

    def list_all(self) -> List[HumanitarianRecord]:
        return list(self.records.values())


record_store = InMemoryRecordStore()
