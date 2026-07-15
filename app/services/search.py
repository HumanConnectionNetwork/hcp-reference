import unicodedata
from collections.abc import Iterable

from app.core.errors import QueryProcessingError
from app.models.humanitarian_record import HumanitarianRecord
from app.models.query import HumanitarianQuery
from app.storage.base import RecordStorage


class SearchService:
    """
    Application service for local Humanitarian Record search.

    Search identifies records that are reasonable candidates for later
    correlation. It intentionally remains broader than correlation and does
    not produce probability scores or identity conclusions.
    """

    def __init__(self, storage: RecordStorage) -> None:
        self.storage = storage

    def search_records(
        self,
        query: HumanitarianQuery,
        limit: int | None = None,
    ) -> list[HumanitarianRecord]:
        """
        Search locally stored Humanitarian Records using partial HCP evidence.

        Candidate selection follows these principles:

        - Subject type must always match.
        - Event type must match when explicitly supplied.
        - Missing record information does not automatically exclude a record.
        - At least one supplied descriptive signal should be compatible when
          descriptive evidence is present.
        - Correlation scoring is performed separately.

        Args:
            query:
                Structured HCP Query containing the available evidence.

            limit:
                Optional maximum number of candidates to return.

        Raises:
            QueryProcessingError:
                If the supplied limit is invalid or search cannot be
                completed.
        """
        if limit is not None and limit < 1:
            raise QueryProcessingError(
                "search limit must be greater than or equal to 1"
            )

        try:
            records = self.storage.list_all()

            candidates = [
                record
                for record in records
                if self._is_candidate(query, record)
            ]

            candidates.sort(
                key=lambda record: record.observation.observed_at,
                reverse=True,
            )

            if limit is not None:
                return candidates[:limit]

            return candidates

        except QueryProcessingError:
            raise

        except Exception as exc:
            raise QueryProcessingError(
                "Unable to process the local Humanitarian Record search"
            ) from exc

    def _is_candidate(
        self,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> bool:
        """
        Determine whether one record should enter the correlation stage.
        """
        if record.subject.type != query.subject.type:
            return False

        if (
            query.observation is not None
            and query.observation.event_type is not None
            and record.observation.event_type
            != query.observation.event_type
        ):
            return False

        descriptive_checks = list(
            self._descriptive_compatibility_checks(query, record)
        )

        if not descriptive_checks:
            return True

        return any(descriptive_checks)

    def _descriptive_compatibility_checks(
        self,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> Iterable[bool]:
        """
        Yield compatibility checks for supplied descriptive evidence.

        Evidence absent from the Query is ignored. Evidence missing from a
        stored record is also ignored rather than treated as a contradiction.
        """
        if (
            query.subject.reported_label is not None
            and record.subject.reported_label is not None
        ):
            yield self._text_is_compatible(
                query.subject.reported_label,
                record.subject.reported_label,
            )

        if (
            query.subject.estimated_age is not None
            and record.subject.estimated_age is not None
        ):
            yield self._age_is_compatible(
                query.subject.estimated_age,
                record.subject.estimated_age,
            )

        if (
            query.subject.recognition_features is not None
            and record.subject.recognition_features is not None
        ):
            yield self._text_is_compatible(
                query.subject.recognition_features,
                record.subject.recognition_features,
            )

        if (
            query.observation is not None
            and query.observation.reported_location is not None
            and record.observation.reported_location is not None
        ):
            yield self._text_is_compatible(
                query.observation.reported_location,
                record.observation.reported_location,
            )

    @staticmethod
    def _age_is_compatible(
        query_age: int,
        record_age: int,
    ) -> bool:
        """
        Apply a broad age tolerance during candidate selection.

        Exact age influence belongs to correlation. The search stage uses a
        deliberately permissive range to avoid excluding useful candidates.
        """
        tolerance = max(5, round(query_age * 0.20))

        return abs(query_age - record_age) <= tolerance

    @classmethod
    def _text_is_compatible(
        cls,
        query_value: str,
        record_value: str,
    ) -> bool:
        """
        Compare descriptive text using normalized containment and token
        overlap.

        This is candidate selection, not final similarity scoring.
        """
        normalized_query = cls._normalize_text(query_value)
        normalized_record = cls._normalize_text(record_value)

        if not normalized_query or not normalized_record:
            return False

        if (
            normalized_query in normalized_record
            or normalized_record in normalized_query
        ):
            return True

        query_tokens = set(normalized_query.split())
        record_tokens = set(normalized_record.split())

        if not query_tokens or not record_tokens:
            return False

        return bool(query_tokens.intersection(record_tokens))

    @staticmethod
    def _normalize_text(value: str) -> str:
        """
        Normalize human-entered text for broad local comparison.

        The normalization:

        - removes surrounding whitespace;
        - uses case-insensitive comparison;
        - removes diacritical marks;
        - replaces punctuation with spaces;
        - collapses repeated whitespace.
        """
        decomposed = unicodedata.normalize(
            "NFKD",
            value.strip().casefold(),
        )

        without_accents = "".join(
            character
            for character in decomposed
            if not unicodedata.combining(character)
        )

        alphanumeric_text = "".join(
            character if character.isalnum() else " "
            for character in without_accents
        )

        return " ".join(alphanumeric_text.split())

