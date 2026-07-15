python
import json
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from app.core.errors import (
    InvalidStorageDataError,
    RecordAlreadyExistsError,
    RecordNotFoundError,
    StorageError,
)
from app.models.humanitarian_record import HumanitarianRecord
from app.storage.base import RecordStorage


class JSONRecordStorage(RecordStorage):
    """
    Local JSON storage implementation for Humanitarian Records.

    Records are stored as a JSON array. The complete collection is loaded and
    validated on every operation so corrupted or non-conforming persisted data
    is never silently accepted.

    This implementation is intentionally simple and educational. Production
    HCP Nodes may replace it with another persistence mechanism while keeping
    the same storage contract.
    """

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def create(self, record: HumanitarianRecord) -> HumanitarianRecord:
        """
        Persist a new Humanitarian Record.

        The operation fails when the record identifier already exists.
        """
        records = self._load_records()

        if any(existing.id == record.id for existing in records):
            raise RecordAlreadyExistsError(str(record.id))

        records.append(record)
        self._write_records(records)

        return record

    def get_by_id(self, record_id: UUID) -> HumanitarianRecord:
        """
        Retrieve one Humanitarian Record by its identifier.
        """
        for record in self._load_records():
            if record.id == record_id:
                return record

        raise RecordNotFoundError(str(record_id))

    def list_all(self) -> list[HumanitarianRecord]:
        """
        Return all locally stored Humanitarian Records.
        """
        return list(self._load_records())

    def exists(self, record_id: UUID) -> bool:
        """
        Return whether a Humanitarian Record exists locally.
        """
        return any(
            record.id == record_id
            for record in self._load_records()
        )

    def _ensure_storage_file(self) -> None:
        """
        Ensure that the parent directory and JSON storage file exist.
        """
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)

            if not self.file_path.exists():
                self.file_path.write_text(
                    "[]\n",
                    encoding="utf-8",
                )

        except OSError as exc:
            raise StorageError(
                f"Unable to initialize JSON storage: {self.file_path}"
            ) from exc

    def _load_records(self) -> list[HumanitarianRecord]:
        """
        Load and validate every persisted Humanitarian Record.
        """
        self._ensure_storage_file()

        try:
            raw_content = self.file_path.read_text(encoding="utf-8")
            raw_data = json.loads(raw_content)

        except json.JSONDecodeError as exc:
            raise InvalidStorageDataError(
                f"Storage file does not contain valid JSON: {self.file_path}"
            ) from exc

        except OSError as exc:
            raise StorageError(
                f"Unable to read JSON storage: {self.file_path}"
            ) from exc

        if not isinstance(raw_data, list):
            raise InvalidStorageDataError(
                "JSON storage root must be an array of Humanitarian Records"
            )

        records: list[HumanitarianRecord] = []

        for index, item in enumerate(raw_data):
            if not isinstance(item, dict):
                raise InvalidStorageDataError(
                    f"Stored item at index {index} must be a JSON object"
                )

            try:
                records.append(
                    HumanitarianRecord.model_validate(item)
                )

            except ValidationError as exc:
                raise InvalidStorageDataError(
                    f"Invalid Humanitarian Record at storage index {index}"
                ) from exc

        return records

    def _write_records(
        self,
        records: list[HumanitarianRecord],
    ) -> None:
        """
        Atomically replace the JSON storage file with validated records.
        """
        self._ensure_storage_file()

        serialized_records = [
            record.model_dump(
                mode="json",
                exclude_none=True,
            )
            for record in records
        ]

        temporary_path = self.file_path.with_suffix(
            f"{self.file_path.suffix}.tmp"
        )

        try:
            temporary_path.write_text(
                json.dumps(
                    serialized_records,
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            temporary_path.replace(self.file_path)

        except OSError as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

            raise StorageError(
                f"Unable to write JSON storage: {self.file_path}"
            ) from exc

