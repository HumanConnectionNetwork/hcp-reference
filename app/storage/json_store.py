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
    Implementación local basada en JSON.

    Esta implementación permanece como almacenamiento de referencia para:

    - desarrollo local;
    - pruebas unitarias;
    - ejemplos educativos;
    - migraciones hacia PostgreSQL.

    Aunque la implementación de producción utilizará PostgreSQL,
    JSONRecordStorage continúa siendo importante porque representa el
    comportamiento esperado del contrato RecordStorage.
    """

    def __init__(
        self,
        file_path: Path,
    ) -> None:
        self.file_path = file_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(
        self,
        record: HumanitarianRecord,
    ) -> HumanitarianRecord:
        """
        Persiste un Humanitarian Record.

        La operación falla cuando el UUID ya existe.
        """
        records = self._load_records()

        if any(
            existing.id == record.id
            for existing in records
        ):
            raise RecordAlreadyExistsError(
                str(record.id)
            )

        records.append(record)

        self._write_records(records)

        return record

    def get_by_id(
        self,
        record_id: UUID,
    ) -> HumanitarianRecord:
        """
        Recupera un Humanitarian Record por UUID.
        """
        for record in self._load_records():
            if record.id == record_id:
                return record

        raise RecordNotFoundError(
            str(record_id)
        )

    def list_all(
        self,
    ) -> list[HumanitarianRecord]:
        """
        Devuelve todos los registros locales.

        Se devuelve una copia independiente para impedir modificaciones
        accidentales sobre la colección interna.
        """
        return list(
            self._load_records()
        )

    def exists(
        self,
        record_id: UUID,
    ) -> bool:
        """
        Comprueba si un UUID ya existe.
        """
        return any(
            record.id == record_id
            for record in self._load_records()
        )

    def create_many(
        self,
        records: list[
            HumanitarianRecord
        ],
    ) -> list[HumanitarianRecord]:
        """
        Persiste varios registros.

        Se valida previamente que ningún UUID del lote exista ya dentro del
        almacenamiento.

        Si aparece un conflicto no se modifica el archivo.
        """
        current_records = (
            self._load_records()
        )

        existing_ids = {
            record.id
            for record in current_records
        }

        duplicated_ids = [
            record.id
            for record in records
            if record.id
            in existing_ids
        ]

        if duplicated_ids:
            raise RecordAlreadyExistsError(
                str(
                    duplicated_ids[0]
                )
            )

        current_records.extend(
            records
        )

        self._write_records(
            current_records
        )

        return list(records)

    def count(
        self,
    ) -> int:
        """
        Devuelve la cantidad de registros almacenados.
        """
        return len(
            self._load_records()
        )

    def close(
        self,
    ) -> None:
        """
        JSON no mantiene conexiones abiertas.

        El método existe únicamente para mantener compatibilidad con futuras
        implementaciones PostgreSQL.
        """
        return

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_storage_file(
        self,
    ) -> None:
        """
        Garantiza la existencia del directorio y del archivo JSON.
        """
        try:
            self.file_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            if not self.file_path.exists():
                self.file_path.write_text(
                    "[]\n",
                    encoding="utf-8",
                )

        except OSError as exc:
            raise StorageError(
                f"Unable to initialize JSON storage: {self.file_path}"
            ) from exc

    def _load_records(
        self,
    ) -> list[HumanitarianRecord]:
        """
        Carga y valida completamente el almacenamiento.

        Ningún documento persistido se devuelve sin pasar nuevamente por la
        validación del modelo HumanitarianRecord.
        """
        self._ensure_storage_file()

        try:
            raw_content = (
                self.file_path.read_text(
                    encoding="utf-8",
                )
            )

            raw_data = json.loads(
                raw_content
            )

        except json.JSONDecodeError as exc:
            raise InvalidStorageDataError(
                f"Storage file does not contain valid JSON: {self.file_path}"
            ) from exc

        except OSError as exc:
            raise StorageError(
                f"Unable to read JSON storage: {self.file_path}"
            ) from exc

        if not isinstance(
            raw_data,
            list,
        ):
            raise InvalidStorageDataError(
                "JSON storage root must be an array of Humanitarian Records"
            )

        validated_records: list[
            HumanitarianRecord
        ] = []

        for index, item in enumerate(
            raw_data
        ):
            if not isinstance(
                item,
                dict,
            ):
                raise InvalidStorageDataError(
                    f"Stored item at index {index} must be a JSON object"
                )

            try:
                validated_records.append(
                    HumanitarianRecord.model_validate(
                        item
                    )
                )

            except ValidationError as exc:
                raise InvalidStorageDataError(
                    f"Invalid Humanitarian Record at storage index {index}"
                ) from exc

        return validated_records

    def _write_records(
        self,
        records: list[
            HumanitarianRecord
        ],
    ) -> None:
        """
        Sustituye completamente el archivo utilizando escritura atómica.

        Primero escribe un archivo temporal y posteriormente reemplaza el
        archivo original.
        """
        self._ensure_storage_file()

        serialized_records = [
            record.model_dump(
                mode="json",
                exclude_none=True,
            )
            for record in records
        ]

        temporary_path = (
            self.file_path.with_suffix(
                f"{self.file_path.suffix}.tmp"
            )
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

            temporary_path.replace(
                self.file_path
            )

        except OSError as exc:
            try:
                temporary_path.unlink(
                    missing_ok=True
                )
            except OSError:
                pass

            raise StorageError(
                f"Unable to write JSON storage: {self.file_path}"
            ) from exc
