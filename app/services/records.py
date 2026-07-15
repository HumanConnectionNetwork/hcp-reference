from uuid import UUID

from app.models.humanitarian_record import HumanitarianRecord
from app.storage.base import RecordStorage


class RecordService:
    """
    Application service for Humanitarian Record operations.

    The service coordinates protocol-aligned models with the configured
    storage implementation. It does not depend on FastAPI or any specific
    persistence technology.
    """

    def __init__(self, storage: RecordStorage) -> None:
        self.storage = storage

    def create_record(
        self,
        record: HumanitarianRecord,
    ) -> HumanitarianRecord:
        """
        Validate and persist one Humanitarian Record.

        The HumanitarianRecord model performs structural validation before
        this method is called. The storage implementation is responsible for
        rejecting duplicate identifiers and persistence failures.
        """
        return self.storage.create(record)

    def get_record(
        self,
        record_id: UUID,
    ) -> HumanitarianRecord:
        """
        Retrieve one Humanitarian Record by its identifier.
        """
        return self.storage.get_by_id(record_id)

    def list_records(self) -> list[HumanitarianRecord]:
        """
        Return all Humanitarian Records stored by the local reference node.
        """
        return self.storage.list_all()

    def record_exists(
        self,
        record_id: UUID,
    ) -> bool:
        """
        Return whether a Humanitarian Record exists locally.
        """
        return self.storage.exists(record_id)

