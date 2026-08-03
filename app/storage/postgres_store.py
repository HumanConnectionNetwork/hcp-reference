import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
    create_engine,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine, URL
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
from app.models.query import HumanitarianQuery
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
    # Subject fields projected for indexed candidate selection
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
    # Observation fields projected for indexed candidate selection
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
    # Complete canonical HCP document
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
    PostgreSQL implementation of RecordStorage.

    The complete canonical Humanitarian Record is stored in `record_payload`
    as JSONB.

    Important fields are additionally projected into relational columns so
    PostgreSQL can later reduce the candidate collection before Python runs
    the final HCP correlation.

    This implementation never creates or changes database tables at runtime.
    PostgreSQL schema changes are managed exclusively through Alembic.
    """

    def __init__(
        self,
        database_url: str | URL | None = None,
        *,
        engine: Engine | None = None,
        pool_pre_ping: bool = True,
        pool_size: int = 3,
        max_overflow: int = 2,
        pool_timeout: int = 30,
        pool_recycle: int = 1_800,
        connect_timeout: int = 10,
        sslmode: str = "require",
        application_name: str = "hcp-reference",
    ) -> None:
        """
        Initialize PostgreSQL storage.

        Supply either:

        - database_url, allowing this class to create and own the Engine;
        - engine, allowing an existing Engine to be injected for tests.

        Both values cannot be supplied simultaneously.

        The default pool allows at most five local connections:

            pool_size=3
            max_overflow=2

        This leaves database capacity available for Alembic migrations,
        administrative operations and other infrastructure processes.
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

        self._owns_engine = engine is None
        self._closed = False

        if engine is not None:
            self.engine = engine
            return

        normalized_url = self._normalize_database_url(
            database_url
        )

        try:
            self.engine = create_engine(
                normalized_url,
                pool_pre_ping=pool_pre_ping,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=pool_timeout,
                pool_recycle=pool_recycle,
                pool_use_lifo=True,
                connect_args={
                    "sslmode": sslmode,
                    "connect_timeout": connect_timeout,
                    "application_name": application_name,
                },
            )

        except (SQLAlchemyError, ValueError, TypeError) as exc:
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
        Atomically persist one Humanitarian Record.

        The UUID is the primary key. A duplicate UUID is translated into the
        canonical RecordAlreadyExistsError.
        """
        self._ensure_open()

        statement = insert(
            humanitarian_records_table
        ).values(
            **self._record_to_row(
                record
            )
        )

        try:
            with self.engine.begin() as connection:
                connection.execute(
                    statement
                )

        except IntegrityError as exc:
            if self._is_unique_violation(exc):
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
        Retrieve and validate one Humanitarian Record by UUID.
        """
        self._ensure_open()

        statement = (
            select(
                humanitarian_records_table.c.record_payload
            )
            .where(
                humanitarian_records_table.c.id
                == record_id
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
        Return all records in deterministic chronological order.

        This method preserves compatibility with the current SearchService.

        It will later be complemented by an indexed candidate query so large
        databases do not need to load every record into application memory.
        """
        self._ensure_open()

        statement = (
            select(
                humanitarian_records_table.c.id,
                humanitarian_records_table.c.record_payload,
            )
            .order_by(
                humanitarian_records_table.c.observed_at.asc(),
                humanitarian_records_table.c.id.asc(),
            )
        )

        try:
            with self.engine.connect() as connection:
                rows = (
                    connection.execute(
                        statement
                    )
                    .mappings()
                    .all()
                )

        except SQLAlchemyError as exc:
            raise StorageError(
                "Unable to list Humanitarian Records from PostgreSQL"
            ) from exc

        return [
            self._payload_to_record(
                payload=row["record_payload"],
                record_id=row["id"],
            )
            for row in rows
        ]

    def search_candidates(
        self,
        query: HumanitarianQuery,
        limit: int = 100,
    ) -> list[HumanitarianRecord]:
        """
        Return a bounded preliminary candidate collection using indexed SQL.

        PostgreSQL performs only inexpensive structural filtering here. It
        does not calculate HCP compatibility, similarity or continuity.

        Applied filters:

        - subject type is always required;
        - country is applied when structured location is available;
        - first administrative level is applied when available.

        SearchService remains responsible for evaluating descriptive and
        semantic evidence such as names, age, recognition features, species,
        breed and spatial compatibility.
        """
        self._ensure_open()

        if limit < 1:
            raise ValueError(
                "candidate search limit must be greater than or equal to 1"
            )

        statement = (
            select(
                humanitarian_records_table.c.id,
                humanitarian_records_table.c.record_payload,
            )
            .where(
                humanitarian_records_table.c.subject_type
                == query.subject.type
            )
        )

        declared_location = query.declared_location()

        if declared_location is not None:
            country_code = self._location_value(
                declared_location,
                "country_code",
                uppercase=True,
            )

            admin_level_1 = self._location_value(
                declared_location,
                "admin_level_1",
            )

            admin_level_1_normalized = (
                self._normalize_search_text(
                    admin_level_1
                )
            )

            if country_code is not None:
                statement = statement.where(
                    humanitarian_records_table.c.country_code
                    == country_code
                )

            if admin_level_1_normalized is not None:
                statement = statement.where(
                    humanitarian_records_table.c.admin_level_1_normalized
                    == admin_level_1_normalized
                )

        statement = (
            statement
            .order_by(
                humanitarian_records_table.c.observed_at.desc(),
                humanitarian_records_table.c.id.asc(),
            )
            .limit(limit)
        )

        try:
            with self.engine.connect() as connection:
                rows = (
                    connection.execute(
                        statement
                    )
                    .mappings()
                    .all()
                )

        except SQLAlchemyError as exc:
            raise StorageError(
                "Unable to search Humanitarian Record candidates in "
                "PostgreSQL"
            ) from exc

        return [
            self._payload_to_record(
                payload=row["record_payload"],
                record_id=row["id"],
            )
            for row in rows
        ]

    def exists(
        self,
        record_id: UUID,
    ) -> bool:
        """
        Check whether a UUID exists without loading the JSONB payload.
        """
        self._ensure_open()

        statement = (
            select(
                humanitarian_records_table.c.id
            )
            .where(
                humanitarian_records_table.c.id
                == record_id
            )
            .limit(1)
        )

        try:
            with self.engine.connect() as connection:
                stored_id = connection.execute(
                    statement
                ).scalar_one_or_none()

        except SQLAlchemyError as exc:
            raise StorageError(
                "Unable to check Humanitarian Record existence in PostgreSQL"
            ) from exc

        return stored_id is not None

    def create_many(
        self,
        records: Iterable[HumanitarianRecord],
    ) -> list[HumanitarianRecord]:
        """
        Persist an entire batch in one PostgreSQL transaction.

        If any record fails, PostgreSQL rolls back the complete operation.
        """
        self._ensure_open()

        record_list = list(records)

        if not record_list:
            return []

        self._validate_batch_ids(
            record_list
        )

        rows = [
            self._record_to_row(record)
            for record in record_list
        ]

        try:
            with self.engine.begin() as connection:
                connection.execute(
                    insert(
                        humanitarian_records_table
                    ),
                    rows,
                )

        except IntegrityError as exc:
            if self._is_unique_violation(exc):
                conflicting_id = self._find_existing_id(
                    record_list
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
        Return the total record count using COUNT(*).
        """
        self._ensure_open()

        statement = (
            select(func.count())
            .select_from(
                humanitarian_records_table
            )
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

    def ping(
        self,
    ) -> bool:
        """
        Verify that PostgreSQL accepts a simple query.

        This method does not create, update or delete any information.
        """
        self._ensure_open()

        try:
            with self.engine.connect() as connection:
                value = connection.execute(
                    text("SELECT 1")
                ).scalar_one()

        except SQLAlchemyError as exc:
            raise StorageError(
                "Unable to connect to PostgreSQL"
            ) from exc

        return value == 1

    def close(
        self,
    ) -> None:
        """
        Release database connections owned by this storage instance.

        The method is idempotent and may safely be called more than once.
        """
        if self._closed:
            return

        if self._owns_engine:
            self.engine.dispose()

        self._closed = True

    # ------------------------------------------------------------------
    # HCP document and relational projection
    # ------------------------------------------------------------------

    @classmethod
    def _record_to_row(
        cls,
        record: HumanitarianRecord,
    ) -> dict[str, Any]:
        """
        Project one canonical HCP document into PostgreSQL columns.

        `record_payload` remains the complete canonical representation.
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

        admin_level_1 = cls._location_value(
            declared_location,
            "admin_level_1",
        )

        admin_level_2 = cls._location_value(
            declared_location,
            "admin_level_2",
        )

        locality = cls._location_value(
            declared_location,
            "locality",
        )

        district = cls._location_value(
            declared_location,
            "district",
        )

        return {
            "id": record.id,
            "schema_version": record.schema_version,
            "source_client": record.source_client,
            "subject_type": subject.type,

            "reported_label": reported_label,
            "reported_label_normalized": (
                cls._normalize_search_text(
                    reported_label
                )
            ),

            "estimated_age": getattr(
                subject,
                "estimated_age",
                None,
            ),

            "recognition_features": recognition_features,
            "recognition_features_normalized": (
                cls._normalize_search_text(
                    recognition_features
                )
            ),

            "species": species,
            "species_normalized": (
                cls._normalize_search_text(
                    species
                )
            ),

            "animal_size": getattr(
                subject,
                "size",
                None,
            ),

            "breed": breed,
            "breed_normalized": (
                cls._normalize_search_text(
                    breed
                )
            ),

            "event_type": observation.event_type,
            "reported_by": observation.reported_by,
            "observed_at": observation.observed_at,

            "country_code": cls._location_value(
                declared_location,
                "country_code",
                uppercase=True,
            ),

            "admin_level_1": admin_level_1,
            "admin_level_1_normalized": (
                cls._normalize_search_text(
                    admin_level_1
                )
            ),

            "admin_level_2": admin_level_2,
            "admin_level_2_normalized": (
                cls._normalize_search_text(
                    admin_level_2
                )
            ),

            "locality": locality,
            "locality_normalized": (
                cls._normalize_search_text(
                    locality
                )
            ),

            "district": district,
            "district_normalized": (
                cls._normalize_search_text(
                    district
                )
            ),

            "legacy_reported_location": getattr(
                observation,
                "reported_location",
                None,
            ),

            "record_payload": record.model_dump(
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
        Reconstruct and validate one canonical record stored as JSONB.
        """
        if not isinstance(payload, Mapping):
            raise InvalidStorageDataError(
                "PostgreSQL record_payload must contain a JSON object"
            )

        try:
            record = HumanitarianRecord.model_validate(
                dict(payload)
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
        records: list[HumanitarianRecord],
    ) -> None:
        """
        Reject duplicate UUIDs contained in the same import batch.
        """
        seen_ids: set[UUID] = set()

        for record in records:
            if record.id in seen_ids:
                raise RecordAlreadyExistsError(
                    str(record.id)
                )

            seen_ids.add(record.id)

    def _find_existing_id(
        self,
        records: list[HumanitarianRecord],
    ) -> UUID | None:
        """
        Attempt to identify an existing UUID following a batch conflict.
        """
        record_ids = [
            record.id
            for record in records
        ]

        statement = (
            select(
                humanitarian_records_table.c.id
            )
            .where(
                humanitarian_records_table.c.id.in_(
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
        Detect PostgreSQL SQLSTATE 23505: unique_violation.
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
    # Connection and normalization helpers
    # ------------------------------------------------------------------

    def _ensure_open(
        self,
    ) -> None:
        if self._closed:
            raise StorageError(
                "PostgreSQL storage is closed"
            )

    @staticmethod
    def _normalize_database_url(
        database_url: str | URL | None,
    ) -> str | URL:
        """
        Ensure string URLs use the SQLAlchemy Psycopg 3 dialect.

        Accepted input prefixes:

            postgres://
            postgresql://
            postgresql+psycopg://
        """
        if database_url is None:
            raise ValueError(
                "database_url cannot be None"
            )

        if isinstance(database_url, URL):
            return database_url

        normalized_url = database_url.strip()

        if not normalized_url:
            raise ValueError(
                "database_url cannot be empty"
            )

        if normalized_url.startswith(
            "postgres://"
        ):
            return normalized_url.replace(
                "postgres://",
                "postgresql+psycopg://",
                1,
            )

        if normalized_url.startswith(
            "postgresql://"
        ):
            return normalized_url.replace(
                "postgresql://",
                "postgresql+psycopg://",
                1,
            )

        if not normalized_url.startswith(
            "postgresql+psycopg://"
        ):
            raise ValueError(
                "database_url must use a PostgreSQL connection scheme"
            )

        return normalized_url

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

        normalized_value = str(value).strip()

        if not normalized_value:
            return None

        if uppercase:
            return normalized_value.upper()

        return normalized_value

    @staticmethod
    def _normalize_search_text(
        value: str | None,
    ) -> str | None:
        """
        Normalize search text without changing the original HCP value.

        The resulting value:

        - is case-insensitive;
        - removes accents;
        - converts punctuation into spaces;
        - collapses repeated whitespace;
        - preserves letters and numbers from supported writing systems.
        """
        if value is None:
            return None

        stripped_value = value.strip()

        if not stripped_value:
            return None

        decomposed = unicodedata.normalize(
            "NFKD",
            stripped_value.casefold(),
        )

        without_accents = "".join(
            character
            for character in decomposed
            if not unicodedata.combining(
                character
            )
        )

        alphanumeric_text = "".join(
            character
            if character.isalnum()
            else " "
            for character in without_accents
        )

        normalized_value = " ".join(
            alphanumeric_text.split()
        )

        return normalized_value or None
