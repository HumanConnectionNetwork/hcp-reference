import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import (
    DateTime,
    Engine,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
    Column,
    create_engine,
    func,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import URL
from sqlalchemy.exc import (
    IntegrityError,
    SQLAlchemyError,
)

from app.core.errors import (
    InvalidStorageDataError,
    RecordAlreadyExistsError,
    RecordNotFoundError,
    StorageError,
)
from app.models.humanitarian_record import HumanitarianRecord
from app.storage.base import RecordStorage


metadata = MetaData()


humanitarian_records_table = Table(
    "humanitarian_records",
    metadata,

    Column(
        "id",
        Uuid(as_uuid=True),
        primary_key=True,
        nullable=False,
    ),

    Column(
        "schema_version",
        String(16),
        nullable=False,
    ),

    Column(
        "source_client",
        String(160),
        nullable=False,
    ),

    # --------------------------------------------------------------
    # Subject fields used for indexed candidate selection
    # --------------------------------------------------------------

    Column(
        "subject_type",
        String(16),
        nullable=False,
    ),

    Column(
        "reported_label",
        Text,
        nullable=True,
    ),

    Column(
        "reported_label_normalized",
        Text,
        nullable=True,
    ),

    Column(
        "estimated_age",
        Integer,
        nullable=True,
    ),

    Column(
        "recognition_features",
        Text,
        nullable=True,
    ),

    Column(
        "recognition_features_normalized",
        Text,
        nullable=True,
    ),

    Column(
        "species",
        String(160),
        nullable=True,
    ),

    Column(
        "species_normalized",
        String(160),
        nullable=True,
    ),

    Column(
        "animal_size",
        String(32),
        nullable=True,
    ),

    Column(
        "breed",
        String(200),
        nullable=True,
    ),

    Column(
        "breed_normalized",
        String(200),
        nullable=True,
    ),

    # --------------------------------------------------------------
    # Observation fields used for indexed candidate selection
    # --------------------------------------------------------------

    Column(
        "event_type",
        String(80),
        nullable=False,
    ),

    Column(
        "reported_by",
        String(80),
        nullable=False,
    ),

    Column(
        "observed_at",
        DateTime(timezone=True),
        nullable=False,
    ),

    Column(
        "country_code",
        String(2),
        nullable=True,
    ),

    Column(
        "admin_level_1",
        Text,
        nullable=True,
    ),

    Column(
        "admin_level_1_normalized",
        Text,
        nullable=True,
    ),

    Column(
        "admin_level_2",
        Text,
        nullable=True,
    ),

    Column(
        "admin_level_2_normalized",
        Text,
        nullable=True,
    ),

    Column(
        "locality",
        Text,
        nullable=True,
    ),

    Column(
        "locality_normalized",
        Text,
        nullable=True,
    ),

    Column(
        "district",
        Text,
        nullable=True,
    ),

    Column(
        "district_normalized",
        Text,
        nullable=True,
    ),

    Column(
        "legacy_reported_location",
        Text,
        nullable=True,
    ),

    # --------------------------------------------------------------
    # Canonical HCP document
    # --------------------------------------------------------------

    Column(
        "record_payload",
        JSONB,
        nullable=False,
    ),

    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)


Index(
    "ix_humanitarian_records_subject_type",
    humanitarian_records_table.c.subject_type,
)

Index(
    "ix_humanitarian_records_observed_at",
    humanitarian_records_table.c.observed_at,
)

Index(
    "ix_humanitarian_records_estimated_age",
    humanitarian_records_table.c.estimated_age,
)

Index(
    "ix_humanitarian_records_reported_label_normalized",
    humanitarian_records_table.c.reported_label_normalized,
)

Index(
    "ix_humanitarian_records_country",
    humanitarian_records_table.c.country_code,
)

Index(
    "ix_humanitarian_records_country_region",
    humanitarian_records_table.c.country_code,
    humanitarian_records_table.c.admin_level_1_normalized,
)

Index(
    "ix_humanitarian_records_spatial_context",
    humanitarian_records_table.c.subject_type,
    humanitarian_records_table.c.country_code,
    humanitarian_records_table.c.admin_level_1_normalized,
    humanitarian_records_table.c.locality_normalized,
)

Index(
    "ix_humanitarian_records_spatial_time",
    humanitarian_records_table.c.subject_type,
    humanitarian_records_table.c.country_code,
    humanitarian_records_table.c.admin_level_1_normalized,
    humanitarian_records_table.c.locality_normalized,
    humanitarian_records_table.c.observed_at,
)


class PostgresRecordStorage(RecordStorage):
    """
    Implementación PostgreSQL de RecordStorage.

    El registro HCP completo se conserva dentro de `record_payload` como
    JSONB. Paralelamente, los campos más importantes se proyectan en columnas
    relacionales para permitir búsquedas indexadas y eficientes.

    Esta implementación no crea ni modifica el esquema automáticamente.
    La estructura de la base debe administrarse mediante migraciones.

    Esto evita que el inicio de la aplicación ejecute cambios implícitos en
    una base de datos de producción.
    """

    def __init__(
        self,
        database_url: str | URL | None = None,
        *,
        engine: Engine | None = None,
        pool_pre_ping: bool = True,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: int = 30,
        pool_recycle: int = 1_800,
    ) -> None:
        """
        Inicializa el almacenamiento PostgreSQL.

        Puede recibirse:

        - `database_url`, para que la clase cree su propio Engine;
        - `engine`, útil para pruebas e inyección de dependencias.

        No deben suministrarse ambos al mismo tiempo.
        """
        if (
            database_url is not None
            and engine is not None
        ):
            raise ValueError(
                "Provide either database_url or engine, not both"
            )

        if (
            database_url is None
            and engine is None
        ):
            raise ValueError(
                "PostgresRecordStorage requires database_url or engine"
            )

        self._owns_engine = (
            engine is None
        )

        if engine is not None:
            self.engine = engine
            return

        try:
            self.engine = create_engine(
                database_url,
                pool_pre_ping=pool_pre_ping,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=pool_timeout,
                pool_recycle=pool_recycle,
                future=True,
            )

        except (SQLAlchemyError, ValueError) as exc:
            raise StorageError(
                "Unable to initialize PostgreSQL storage"
            ) from exc

    # ------------------------------------------------------------------
    # RecordStorage public API
    # ------------------------------------------------------------------

    def create(
        self,
        record: HumanitarianRecord,
    ) -> HumanitarianRecord:
        """
        Persiste atómicamente un Humanitarian Record.

        El UUID es la clave primaria. Una colisión se traduce al error
        canónico RecordAlreadyExistsError.
        """
        values = self._record_to_row(
            record
        )

        statement = insert(
            humanitarian_records_table
        ).values(**values)

        try:
            with self.engine.begin() as connection:
                connection.execute(
                    statement
                )

        except IntegrityError as exc:
            if self._is_unique_violation(
                exc
            ):
                raise RecordAlreadyExistsError(
                    str(record.id)
                ) from exc

            raise StorageError(
                "Unable to persist Humanitarian Record in PostgreSQL"
            ) from exc

        except SQLAlchemyError as exc:
            raise StorageError(
                "Unable to persist Humanitarian Record in PostgreSQL"
            ) from exc

        return record

    def get_by_id(
        self,
        record_id: UUID,
    ) -> HumanitarianRecord:
        """
        Recupera y valida un Humanitarian Record mediante su UUID.
        """
        statement = (
            select(
                humanitarian_records_table
                .c.record_payload
            )
            .where(
                humanitarian_records_table
                .c.id == record_id
            )
        )

        try:
            with self.engine.connect() as connection:
                payload = connection.execute(
                    statement
                ).scalar_one_or_none()

        except SQLAlchemyError as exc:
            raise StorageError(
                "Unable to read Humanitarian Record from PostgreSQL"
            ) from exc

        if payload is None:
            raise RecordNotFoundError(
                str(record_id)
            )

        return self._payload_to_record(
            payload=payload,
            record_id=record_id,
        )

    def list_all(
        self,
    ) -> list[HumanitarianRecord]:
        """
        Devuelve todos los registros en orden cronológico determinístico.

        Este método conserva el funcionamiento actual del motor HCP.

        Más adelante, SearchService podrá utilizar consultas indexadas de
        candidatos sin cargar toda la tabla.
        """
        statement = (
            select(
                humanitarian_records_table
                .c.id,
                humanitarian_records_table
                .c.record_payload,
            )
            .order_by(
                humanitarian_records_table
                .c.observed_at.asc(),
                humanitarian_records_table
                .c.id.asc(),
            )
        )

        try:
            with self.engine.connect() as connection:
                rows = connection.execute(
                    statement
                ).mappings().all()

        except SQLAlchemyError as exc:
            raise StorageError(
                "Unable to list Humanitarian Records from PostgreSQL"
            ) from exc

        records: list[
            HumanitarianRecord
        ] = []

        for row in rows:
            records.append(
                self._payload_to_record(
                    payload=row[
                        "record_payload"
                    ],
                    record_id=row["id"],
                )
            )

        return records

    def exists(
        self,
        record_id: UUID,
    ) -> bool:
        """
        Comprueba la existencia del UUID sin cargar el JSONB completo.
        """
        statement = select(
            humanitarian_records_table
            .c.id
        ).where(
            humanitarian_records_table
            .c.id == record_id
        ).limit(1)

        try:
            with self.engine.connect() as connection:
                return (
                    connection.execute(
                        statement
                    ).scalar_one_or_none()
                    is not None
                )

        except SQLAlchemyError as exc:
            raise StorageError(
                "Unable to check Humanitarian Record existence in PostgreSQL"
            ) from exc

    def create_many(
        self,
        records: Iterable[
            HumanitarianRecord
        ],
    ) -> list[HumanitarianRecord]:
        """
        Persiste un lote completo dentro de una única transacción.

        Si algún UUID genera conflicto o se produce otro error, PostgreSQL
        revierte toda la operación.
        """
        record_list = list(records)

        if not record_list:
            return []

        self._validate_batch_ids(
            record_list
        )

        values = [
            self._record_to_row(
                record
            )
            for record in record_list
        ]

        statement = insert(
            humanitarian_records_table
        )

        try:
            with self.engine.begin() as connection:
                connection.execute(
                    statement,
                    values,
                )

        except IntegrityError as exc:
            if self._is_unique_violation(
                exc
            ):
                conflicting_id = (
                    self._find_existing_id(
                        record_list
                    )
                )

                raise RecordAlreadyExistsError(
                    str(
                        conflicting_id
                        or record_list[0].id
                    )
                ) from exc

            raise StorageError(
                "Unable to persist Humanitarian Record batch in PostgreSQL"
            ) from exc

        except SQLAlchemyError as exc:
            raise StorageError(
                "Unable to persist Humanitarian Record batch in PostgreSQL"
            ) from exc

        return list(record_list)

    def count(
        self,
    ) -> int:
        """
        Devuelve la cantidad total mediante COUNT(*).
        """
        statement = select(
            func.count()
        ).select_from(
            humanitarian_records_table
        )

        try:
            with self.engine.connect() as connection:
                value = connection.execute(
                    statement
                ).scalar_one()

        except SQLAlchemyError as exc:
            raise StorageError(
                "Unable to count Humanitarian Records in PostgreSQL"
            ) from exc

        return int(value)

    def close(
        self,
    ) -> None:
        """
        Libera las conexiones administradas por el Engine.

        Solo cierra el Engine cuando fue creado por esta instancia. Un Engine
        inyectado pertenece al código que lo suministró.
        """
        if self._owns_engine:
            self.engine.dispose()

    # ------------------------------------------------------------------
    # Conversion between HCP and relational representation
    # ------------------------------------------------------------------

    @classmethod
    def _record_to_row(
        cls,
        record: HumanitarianRecord,
    ) -> dict[str, Any]:
        """
        Proyecta el documento HCP en columnas relacionales y JSONB.

        `record_payload` continúa siendo la fuente canónica completa.
        """
        subject = record.subject
        observation = record.observation

        declared_location = getattr(
            observation,
            "declared_location",
            None,
        )

        reported_label = getattr(
            subject,
            "reported_label",
            None,
        )

        recognition_features = getattr(
            subject,
            "recognition_features",
            None,
        )

        species = getattr(
            subject,
            "species",
            None,
        )

        breed = getattr(
            subject,
            "breed",
            None,
        )

        return {
            "id":
                record.id,

            "schema_version":
                record.schema_version,

            "source_client":
                record.source_client,

            "subject_type":
                subject.type,

            "reported_label":
                reported_label,

            "reported_label_normalized":
                cls._normalize_search_text(
                    reported_label
                ),

            "estimated_age":
                getattr(
                    subject,
                    "estimated_age",
                    None,
                ),

            "recognition_features":
                recognition_features,

            "recognition_features_normalized":
                cls._normalize_search_text(
                    recognition_features
                ),

            "species":
                species,

            "species_normalized":
                cls._normalize_search_text(
                    species
                ),

            "animal_size":
                getattr(
                    subject,
                    "size",
                    None,
                ),

            "breed":
                breed,

            "breed_normalized":
                cls._normalize_search_text(
                    breed
                ),

            "event_type":
                observation.event_type,

            "reported_by":
                observation.reported_by,

            "observed_at":
                observation.observed_at,

            "country_code":
                cls._location_value(
                    declared_location,
                    "country_code",
                    uppercase=True,
                ),

            "admin_level_1":
                cls._location_value(
                    declared_location,
                    "admin_level_1",
                ),

            "admin_level_1_normalized":
                cls._normalize_search_text(
                    cls._location_value(
                        declared_location,
                        "admin_level_1",
                    )
                ),

            "admin_level_2":
                cls._location_value(
                    declared_location,
                    "admin_level_2",
                ),

            "admin_level_2_normalized":
                cls._normalize_search_text(
                    cls._location_value(
                        declared_location,
                        "admin_level_2",
                    )
                ),

            "locality":
                cls._location_value(
                    declared_location,
                    "locality",
                ),

            "locality_normalized":
                cls._normalize_search_text(
                    cls._location_value(
                        declared_location,
                        "locality",
                    )
                ),

            "district":
                cls._location_value(
                    declared_location,
                    "district",
                ),

            "district_normalized":
                cls._normalize_search_text(
                    cls._location_value(
                        declared_location,
                        "district",
                    )
                ),

            "legacy_reported_location":
                getattr(
                    observation,
                    "reported_location",
                    None,
                ),

            "record_payload":
                record.model_dump(
                    mode="json",
                    exclude_none=True,
                ),
        }

    @staticmethod
    def _payload_to_record(
        payload: object,
        record_id: UUID,
    ) -> HumanitarianRecord:
        """
        Reconstruye y valida el documento canónico almacenado en JSONB.
        """
        if not isinstance(
            payload,
            Mapping,
        ):
            raise InvalidStorageDataError(
                "PostgreSQL record_payload must contain a JSON object"
            )

        try:
            record = (
                HumanitarianRecord
                .model_validate(
                    dict(payload)
                )
            )

        except ValidationError as exc:
            raise InvalidStorageDataError(
                "PostgreSQL contains an invalid Humanitarian Record "
                f"for identifier {record_id}"
            ) from exc

        if record.id != record_id:
            raise InvalidStorageDataError(
                "PostgreSQL row identifier does not match record_payload "
                f"identifier for {record_id}"
            )

        return record

    # ------------------------------------------------------------------
    # Batch and database error helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_batch_ids(
        records: list[
            HumanitarianRecord
        ],
    ) -> None:
        """
        Rechaza identificadores repetidos dentro del mismo lote.
        """
        seen_ids: set[UUID] = set()

        for record in records:
            if record.id in seen_ids:
                raise RecordAlreadyExistsError(
                    str(record.id)
                )

            seen_ids.add(
                record.id
            )

    def _find_existing_id(
        self,
        records: list[
            HumanitarianRecord
        ],
    ) -> UUID | None:
        """
        Intenta identificar el UUID que ya existe tras un conflicto de lote.
        """
        record_ids = [
            record.id
            for record in records
        ]

        statement = (
            select(
                humanitarian_records_table
                .c.id
            )
            .where(
                humanitarian_records_table
                .c.id.in_(
                    record_ids
                )
            )
            .limit(1)
        )

        try:
            with self.engine.connect() as connection:
                return connection.execute(
                    statement
                ).scalar_one_or_none()

        except SQLAlchemyError:
            return None

    @staticmethod
    def _is_unique_violation(
        error: IntegrityError,
    ) -> bool:
        """
        Reconoce una violación UNIQUE de PostgreSQL.

        SQLSTATE 23505 representa unique_violation.
        """
        original_error = error.orig

        sqlstate = getattr(
            original_error,
            "sqlstate",
            None,
        )

        if sqlstate == "23505":
            return True

        diagnostic = getattr(
            original_error,
            "diag",
            None,
        )

        return (
            getattr(
                diagnostic,
                "sqlstate",
                None,
            )
            == "23505"
        )

    # ------------------------------------------------------------------
    # Normalization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _location_value(
        location: object | None,
        field_name: str,
        *,
        uppercase: bool = False,
    ) -> str | None:
        if location is None:
            return None

        value = getattr(
            location,
            field_name,
            None,
        )

        if value is None:
            return None

        normalized_value = str(
            value
        ).strip()

        if not normalized_value:
            return None

        if uppercase:
            return (
                normalized_value
                .upper()
            )

        return normalized_value

    @staticmethod
    def _normalize_search_text(
        value: str | None,
    ) -> str | None:
        """
        Normaliza texto de búsqueda de forma determinística.

        Esta proyección no sustituye el contenido original del protocolo.
        Solo produce una columna auxiliar indexable.
        """
        if value is None:
            return None

        stripped_value = (
            value.strip()
        )

        if not stripped_value:
            return None

        decomposed = (
            unicodedata.normalize(
                "NFKD",
                stripped_value.casefold(),
            )
        )

        without_accents = "".join(
            character
            for character in decomposed
            if not unicodedata.combining(
                character
            )
        )

        normalized_value = re.sub(
            r"[^a-z0-9]+",
            " ",
            without_accents,
        ).strip()

        return (
            normalized_value
            or None
        )
