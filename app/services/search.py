import unicodedata
from datetime import datetime
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.core.errors import QueryProcessingError
from app.core.search_settings import SearchSettings
from app.models.humanitarian_record import HumanitarianRecord
from app.models.query import HumanitarianQuery
from app.storage.base import RecordStorage


@dataclass(frozen=True)
class _CandidateEvaluation:
    """
    Internal candidate-selection result.

    This score is used only to order and filter records before correlation.
    It is not an HCP correlation percentage and must not be presented to users.
    """

    record: HumanitarianRecord

    spatial_rank: int

    descriptive_rank: float


class SearchService:
    """
    Application service for local Humanitarian Record candidate selection.

    Search narrows the local record collection before the correlation stage.

    The selection follows the HCP space-time model:

    1. where: declared country and geographic hierarchy;
    2. when: elapsed time and displacement plausibility;
    3. who: reported name or label;
    4. age: estimated human age;
    5. characteristics: recognition evidence and animal descriptors.

    Search does not:

    - compare event types;
    - confirm identity;
    - calculate the final compatibility percentage;
    - build a Humanitarian Case;
    - merge unrelated records into one history.
    """

    # Spatial hierarchy used only for candidate ordering.
    #
    # The ordering follows the practical HCP sequence:
    #
    #     where -> when -> who -> age -> characteristics
    #
    # Same-country candidates always outrank exceptional international ones.
    SPATIAL_EXACT_LOCALITY = 5
    SPATIAL_SAME_REGION = 4
    SPATIAL_SAME_COUNTRY = 3
    SPATIAL_LEGACY_COMPATIBLE = 2
    SPATIAL_INTERNATIONAL_PLAUSIBLE = 1
    SPATIAL_UNAVAILABLE = 0

    # Candidate-stage tolerances remain broader than final correlation.
    STRONG_NAME_SIMILARITY = 0.70
    PARTIAL_NAME_SIMILARITY = 0.52
    FEATURE_SIMILARITY = 0.22
    AGE_TOLERANCE = 5

    # Cross-country exception.
    #
    # A record declared in another country is normally excluded. It may reach
    # final correlation only when at least 72 hours have elapsed and the
    # descriptive evidence is exceptionally strong.
    INTERNATIONAL_MINIMUM_HOURS = 72.0
    INTERNATIONAL_PERSON_NAME_SIMILARITY = 0.92
    INTERNATIONAL_PERSON_FEATURE_SIMILARITY = 0.60
    INTERNATIONAL_PERSON_AGE_TOLERANCE = 3
    INTERNATIONAL_ANIMAL_SPECIES_SIMILARITY = 0.90
    INTERNATIONAL_ANIMAL_BREED_SIMILARITY = 0.75
    INTERNATIONAL_ANIMAL_FEATURE_SIMILARITY = 0.60

    def __init__(
        self,
        storage: RecordStorage,
        settings: SearchSettings | None = None,
    ) -> None:
        self.storage = storage
        self.settings = settings or SearchSettings()

    def search_records(
        self,
        query: HumanitarianQuery,
        limit: int | None = None,
    ) -> list[HumanitarianRecord]:
        """
        Return reasonable records for the later correlation stage.

        Important rules:

        - subject type must match;
        - event type never excludes a candidate;
        - another declared country is excluded during the first 72 hours;
        - after 72 hours, another country is considered only as an exceptional
          candidate with very strong descriptive evidence;
        - same-country records from another region remain lower-priority
          candidates and must still pass descriptive evaluation;
        - district differences do not exclude records from the same locality;
        - a common descriptive word by itself is not sufficient;
        - a partial name such as "Maria" may match "Maria Atencio";
        - age differences of up to five years remain candidates so that the
          correlation service can apply the stricter ±3 interpretation;
        - legacy 0.5 locations remain usable only when their free text is
          geographically compatible with the structured query.
        """
        if limit is not None and limit < 1:
            raise QueryProcessingError(
                "search limit must be greater than or equal to 1"
            )

        try:
            candidate_limit = self._candidate_fetch_limit(
                result_limit=limit,
            )

            candidate_records = self.storage.search_candidates(
                query=query,
                limit=candidate_limit,
            )

            evaluations = [
                evaluation
                for record in candidate_records
                if (
                    evaluation := self._evaluate_candidate(
                        query=query,
                        record=record,
                    )
                )
                is not None
            ]

            evaluations.sort(
                key=lambda evaluation: (
                    evaluation.spatial_rank,
                    evaluation.descriptive_rank,
                    evaluation.record.observation.observed_at,
                ),
                reverse=True,
            )

            records = [
                evaluation.record
                for evaluation in evaluations
            ]

            if limit is not None:
                return records[:limit]

            return records

        except QueryProcessingError:
            raise

        except Exception as exc:
            raise QueryProcessingError(
                "Unable to process the local Humanitarian Record search"
            ) from exc

    def _candidate_fetch_limit(
        self,
        result_limit: int | None,
    ) -> int:
        """
        Calculate the preliminary storage window for candidate evaluation.

        Storage-level filters are intentionally broader and cheaper than the
        semantic rules applied by SearchService. A wider preliminary window
        therefore prevents valid matches from being lost before descriptive
        evaluation while still keeping PostgreSQL reads bounded.

        Args:
            result_limit:
                Maximum number of evaluated records requested by the caller.

        Returns:
            Maximum number of preliminary records requested from storage.
        """
        if result_limit is None:
            return self.settings.candidate_fetch_limit

        scaled_limit = (
            result_limit
            * self.settings.candidate_multiplier
        )

        return min(
            self.settings.max_candidate_fetch_limit,
            max(
                self.settings.candidate_fetch_limit,
                scaled_limit,
            ),
        )

    def _evaluate_candidate(
        self,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> _CandidateEvaluation | None:
        """
        Decide whether one record should reach final correlation.
        """
        if record.subject.type != query.subject.type:
            return None

        spatial_rank = self._spatial_rank(
            query=query,
            record=record,
        )

        if spatial_rank is None:
            return None

        descriptive_rank = self._descriptive_rank(
            query=query,
            record=record,
        )

        if descriptive_rank is None:
            return None

        return _CandidateEvaluation(
            record=record,
            spatial_rank=spatial_rank,
            descriptive_rank=descriptive_rank,
        )

    def _spatial_rank(
        self,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> int | None:
        """
        Apply hierarchical geographic candidate selection.

        Structured 0.6 locations are compared by:

        country -> first administrative level -> locality -> district.

        Country is the first spatial gate. Another country is normally
        excluded and becomes exceptionally plausible only after 72 hours with
        very strong descriptive support.

        Within the same country, region and locality determine candidate
        priority. District is supporting context and never excludes a
        candidate by itself.
        """
        query_location = self._declared_location(
            getattr(
                query,
                "observation",
                None,
            )
        )

        if query_location is None:
            return self.SPATIAL_UNAVAILABLE

        record_location = self._declared_location(
            record.observation
        )

        if record_location is not None:
            query_country = self._normalize_code(
                getattr(
                    query_location,
                    "country_code",
                    None,
                )
            )
            record_country = self._normalize_code(
                getattr(
                    record_location,
                    "country_code",
                    None,
                )
            )

            if (
                query_country
                and record_country
                and query_country != record_country
            ):
                if self._international_candidate_is_plausible(
                    query=query,
                    record=record,
                ):
                    return self.SPATIAL_INTERNATIONAL_PLAUSIBLE

                return None

            query_region = self._normalize_text(
                getattr(
                    query_location,
                    "admin_level_1",
                    "",
                )
            )
            record_region = self._normalize_text(
                getattr(
                    record_location,
                    "admin_level_1",
                    "",
                )
            )

            if (
                query_region
                and record_region
                and not self._location_text_matches(
                    query_region,
                    record_region,
                )
            ):
                return self.SPATIAL_SAME_COUNTRY

            query_locality = self._normalize_text(
                getattr(
                    query_location,
                    "locality",
                    "",
                )
            )
            record_locality = self._normalize_text(
                getattr(
                    record_location,
                    "locality",
                    "",
                )
            )

            if (
                query_locality
                and record_locality
                and self._location_text_matches(
                    query_locality,
                    record_locality,
                )
            ):
                return self.SPATIAL_EXACT_LOCALITY

            return self.SPATIAL_SAME_REGION

        legacy_location = getattr(
            record.observation,
            "reported_location",
            None,
        )

        if not legacy_location:
            return self.SPATIAL_UNAVAILABLE

        if self._legacy_location_matches_query(
            query_location=query_location,
            legacy_location=legacy_location,
        ):
            return self.SPATIAL_LEGACY_COMPATIBLE

        return None

    def _international_candidate_is_plausible(
        self,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> bool:
        """
        Apply the exceptional cross-country space-time gate.
        """
        elapsed_hours = self._elapsed_hours(
            query=query,
            record=record,
        )

        if (
            elapsed_hours is None
            or elapsed_hours < self.INTERNATIONAL_MINIMUM_HOURS
        ):
            return False

        if query.subject.type == "animal":
            return self._international_animal_support(
                query=query,
                record=record,
            )

        return self._international_person_support(
            query=query,
            record=record,
        )

    def _international_person_support(
        self,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> bool:
        query_subject = query.subject
        record_subject = record.subject

        name_similarity = self._optional_text_similarity(
            query_subject.reported_label,
            record_subject.reported_label,
        )

        if (
            name_similarity is None
            or name_similarity
            < self.INTERNATIONAL_PERSON_NAME_SIMILARITY
        ):
            return False

        query_age = getattr(
            query_subject,
            "estimated_age",
            None,
        )
        record_age = getattr(
            record_subject,
            "estimated_age",
            None,
        )

        if query_age is not None:
            if record_age is None:
                return False

            if (
                abs(query_age - record_age)
                > self.INTERNATIONAL_PERSON_AGE_TOLERANCE
            ):
                return False

        query_features = getattr(
            query_subject,
            "recognition_features",
            None,
        )

        if query_features:
            feature_similarity = self._optional_text_similarity(
                query_features,
                getattr(
                    record_subject,
                    "recognition_features",
                    None,
                ),
            )

            if (
                feature_similarity is None
                or feature_similarity
                < self.INTERNATIONAL_PERSON_FEATURE_SIMILARITY
            ):
                return False

        return bool(
            query_age is not None
            or query_features
        )

    def _international_animal_support(
        self,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> bool:
        query_subject = query.subject
        record_subject = record.subject

        species_similarity = self._optional_text_similarity(
            getattr(
                query_subject,
                "species",
                None,
            ),
            getattr(
                record_subject,
                "species",
                None,
            ),
        )

        if (
            species_similarity is None
            or species_similarity
            < self.INTERNATIONAL_ANIMAL_SPECIES_SIMILARITY
        ):
            return False

        required_secondary_signals = 0
        matched_secondary_signals = 0

        query_breed = getattr(
            query_subject,
            "breed",
            None,
        )

        if query_breed:
            required_secondary_signals += 1
            breed_similarity = self._optional_text_similarity(
                query_breed,
                getattr(
                    record_subject,
                    "breed",
                    None,
                ),
            )

            if (
                breed_similarity is not None
                and breed_similarity
                >= self.INTERNATIONAL_ANIMAL_BREED_SIMILARITY
            ):
                matched_secondary_signals += 1

        query_size = self._normalize_text(
            getattr(
                query_subject,
                "size",
                None,
            )
            or ""
        )

        if query_size:
            required_secondary_signals += 1
            record_size = self._normalize_text(
                getattr(
                    record_subject,
                    "size",
                    None,
                )
                or ""
            )

            if query_size == record_size:
                matched_secondary_signals += 1

        query_features = getattr(
            query_subject,
            "recognition_features",
            None,
        )

        if query_features:
            required_secondary_signals += 1
            feature_similarity = self._optional_text_similarity(
                query_features,
                getattr(
                    record_subject,
                    "recognition_features",
                    None,
                ),
            )

            if (
                feature_similarity is not None
                and feature_similarity
                >= self.INTERNATIONAL_ANIMAL_FEATURE_SIMILARITY
            ):
                matched_secondary_signals += 1

        if required_secondary_signals == 0:
            return False

        return matched_secondary_signals == required_secondary_signals

    @classmethod
    def _elapsed_hours(
        cls,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> float | None:
        query_observation = getattr(
            query,
            "observation",
            None,
        )

        if query_observation is None:
            return None

        searched_at = cls._as_datetime(
            getattr(
                query_observation,
                "searched_at",
                None,
            )
        )
        observed_at = cls._as_datetime(
            getattr(
                record.observation,
                "observed_at",
                None,
            )
        )

        if searched_at is None or observed_at is None:
            return None

        elapsed_seconds = (
            searched_at - observed_at
        ).total_seconds()

        if elapsed_seconds < 0:
            return None

        return elapsed_seconds / 3_600.0

    def _descriptive_rank(
        self,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> float | None:
        """
        Evaluate whether descriptive evidence is strong enough for candidacy.

        A generic feature overlap alone is deliberately insufficient.

        Preferred paths:

        - compatible name;
        - compatible age plus compatible characteristics;
        - animals: compatible species plus another descriptive signal.
        """
        query_subject = query.subject
        record_subject = record.subject

        name_similarity = self._optional_text_similarity(
            query_subject.reported_label,
            record_subject.reported_label,
        )

        age_compatible = self._optional_age_compatible(
            getattr(
                query_subject,
                "estimated_age",
                None,
            ),
            getattr(
                record_subject,
                "estimated_age",
                None,
            ),
        )

        feature_similarity = self._optional_text_similarity(
            query_subject.recognition_features,
            record_subject.recognition_features,
        )

        name_is_strong = (
            name_similarity is not None
            and name_similarity >= self.STRONG_NAME_SIMILARITY
        )

        name_is_partial = (
            name_similarity is not None
            and name_similarity >= self.PARTIAL_NAME_SIMILARITY
        )

        features_are_compatible = (
            feature_similarity is not None
            and feature_similarity >= self.FEATURE_SIMILARITY
        )

        supplied_name = bool(
            self._normalize_text(
                query_subject.reported_label or ""
            )
        )

        supplied_age = (
            getattr(
                query_subject,
                "estimated_age",
                None,
            )
            is not None
        )

        supplied_features = bool(
            self._normalize_text(
                query_subject.recognition_features or ""
            )
        )

        # Name is the strongest descriptive anchor. A partial query name such
        # as "Maria" should retain "Maria Atencio".
        if supplied_name:
            if not name_is_partial:
                return None

            rank = (name_similarity or 0.0) * 60.0

            if age_compatible is True:
                rank += 25.0

            if features_are_compatible:
                rank += 15.0

            return round(rank, 4)

        # Without a name, do not accept a candidate merely because it shares
        # one common word. Require age and feature compatibility together.
        if supplied_age and supplied_features:
            if (
                age_compatible is not True
                or not features_are_compatible
            ):
                return None

            return round(
                55.0
                + (feature_similarity or 0.0) * 30.0,
                4,
            )

        if query_subject.type == "animal":
            return self._animal_descriptive_rank(
                query=query,
                record=record,
                name_similarity=name_similarity,
                feature_similarity=feature_similarity,
                age_compatible=age_compatible,
            )

        # A strong full-name comparison remains useful even when no age or
        # characteristics were supplied.
        if name_is_strong:
            return round(
                (name_similarity or 0.0) * 100.0,
                4,
            )

        return None

    def _animal_descriptive_rank(
        self,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
        name_similarity: float | None,
        feature_similarity: float | None,
        age_compatible: bool | None,
    ) -> float | None:
        """
        Evaluate animal-specific evidence.

        `age_compatible` is accepted only to keep one shared method signature;
        animal records do not use estimated human age.
        """
        del age_compatible

        query_subject = query.subject
        record_subject = record.subject

        species_similarity = self._optional_text_similarity(
            getattr(
                query_subject,
                "species",
                None,
            ),
            getattr(
                record_subject,
                "species",
                None,
            ),
        )

        breed_similarity = self._optional_text_similarity(
            getattr(
                query_subject,
                "breed",
                None,
            ),
            getattr(
                record_subject,
                "breed",
                None,
            ),
        )

        query_size = self._normalize_text(
            getattr(
                query_subject,
                "size",
                None,
            )
            or ""
        )
        record_size = self._normalize_text(
            getattr(
                record_subject,
                "size",
                None,
            )
            or ""
        )

        size_matches = bool(
            query_size
            and record_size
            and query_size == record_size
        )

        species_matches = (
            species_similarity is not None
            and species_similarity >= 0.70
        )

        secondary_matches = sum(
            [
                bool(
                    name_similarity is not None
                    and name_similarity
                    >= self.PARTIAL_NAME_SIMILARITY
                ),
                bool(
                    feature_similarity is not None
                    and feature_similarity
                    >= self.FEATURE_SIMILARITY
                ),
                bool(
                    breed_similarity is not None
                    and breed_similarity >= 0.45
                ),
                size_matches,
            ]
        )

        if not species_matches:
            return None

        # Species by itself is too broad: "perro" must be accompanied by at
        # least one more useful signal.
        if secondary_matches < 1:
            return None

        rank = 45.0

        rank += min(
            (species_similarity or 0.0) * 20.0,
            20.0,
        )

        if name_similarity is not None:
            rank += min(
                name_similarity * 15.0,
                15.0,
            )

        if feature_similarity is not None:
            rank += min(
                feature_similarity * 12.0,
                12.0,
            )

        if breed_similarity is not None:
            rank += min(
                breed_similarity * 5.0,
                5.0,
            )

        if size_matches:
            rank += 3.0

        return round(rank, 4)

    @classmethod
    def _optional_text_similarity(
        cls,
        query_value: str | None,
        record_value: str | None,
    ) -> float | None:
        if query_value is None:
            return None

        if record_value is None:
            return 0.0

        return cls._text_similarity(
            query_value,
            record_value,
        )

    @classmethod
    def _text_similarity(
        cls,
        query_value: str,
        record_value: str,
    ) -> float:
        """
        Compare human-entered text using containment, tokens and sequence.

        This deliberately rewards a known first name contained in a longer
        reported name:

        "Maria" -> "Maria Atencio"

        It also tolerates accents and small typing differences.
        """
        normalized_query = cls._normalize_text(
            query_value
        )
        normalized_record = cls._normalize_text(
            record_value
        )

        if not normalized_query or not normalized_record:
            return 0.0

        if normalized_query == normalized_record:
            return 1.0

        if (
            normalized_query in normalized_record
            or normalized_record in normalized_query
        ):
            shorter = min(
                len(normalized_query),
                len(normalized_record),
            )
            longer = max(
                len(normalized_query),
                len(normalized_record),
            )

            length_ratio = (
                shorter / longer
                if longer
                else 0.0
            )

            # Containment receives a high floor because partial names are an
            # expected search pattern, not an accidental fuzzy match.
            return max(
                0.78,
                length_ratio,
            )

        query_tokens = set(
            normalized_query.split()
        )
        record_tokens = set(
            normalized_record.split()
        )

        token_union = (
            query_tokens | record_tokens
        )
        token_intersection = (
            query_tokens & record_tokens
        )

        token_similarity = (
            len(token_intersection)
            / len(token_union)
            if token_union
            else 0.0
        )

        sequence_similarity = (
            SequenceMatcher(
                None,
                normalized_query,
                normalized_record,
            ).ratio()
        )

        return max(
            token_similarity,
            sequence_similarity,
        )

    @classmethod
    def _location_text_matches(
        cls,
        query_value: str,
        record_value: str,
    ) -> bool:
        similarity = cls._text_similarity(
            query_value,
            record_value,
        )

        return similarity >= 0.72

    @classmethod
    def _legacy_location_matches_query(
        cls,
        query_location: object,
        legacy_location: str,
    ) -> bool:
        """
        Compare a legacy free-text 0.5 location with structured query levels.
        """
        normalized_legacy = cls._normalize_text(
            legacy_location
        )

        if not normalized_legacy:
            return False

        relevant_parts = [
            getattr(
                query_location,
                "locality",
                None,
            ),
            getattr(
                query_location,
                "admin_level_2",
                None,
            ),
            getattr(
                query_location,
                "admin_level_1",
                None,
            ),
        ]

        return any(
            cls._text_is_contained(
                part,
                normalized_legacy,
            )
            for part in relevant_parts
            if part
        )

    @classmethod
    def _text_is_contained(
        cls,
        value: str,
        normalized_container: str,
    ) -> bool:
        normalized_value = cls._normalize_text(
            value
        )

        return bool(
            normalized_value
            and (
                normalized_value
                in normalized_container
                or normalized_container
                in normalized_value
            )
        )

    @staticmethod
    def _optional_age_compatible(
        query_age: int | None,
        record_age: int | None,
    ) -> bool | None:
        if query_age is None:
            return None

        if record_age is None:
            return False

        return (
            abs(query_age - record_age)
            <= SearchService.AGE_TOLERANCE
        )

    @staticmethod
    def _declared_location(
        observation: object | None,
    ) -> object | None:
        if observation is None:
            return None

        return getattr(
            observation,
            "declared_location",
            None,
        )

    @staticmethod
    def _as_datetime(
        value: object,
    ) -> datetime | None:
        if isinstance(value, datetime):
            return value

        if isinstance(value, str):
            try:
                return datetime.fromisoformat(
                    value.replace(
                        "Z",
                        "+00:00",
                    )
                )
            except ValueError:
                return None

        return None

    @staticmethod
    def _normalize_code(
        value: str | None,
    ) -> str:
        if value is None:
            return ""

        return value.strip().upper()

    @staticmethod
    def _normalize_text(
        value: str,
    ) -> str:
        """
        Normalize human-entered text for local comparison.

        The normalization:

        - trims whitespace;
        - uses case-insensitive comparison;
        - removes accents;
        - converts punctuation to spaces;
        - collapses repeated whitespace.
        """
        decomposed = unicodedata.normalize(
            "NFKD",
            value.strip().casefold(),
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

        return " ".join(
            alphanumeric_text.split()
        )
