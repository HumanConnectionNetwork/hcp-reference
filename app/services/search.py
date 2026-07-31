import unicodedata
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher

from app.core.errors import QueryProcessingError
from app.models.humanitarian_record import (
    DeclaredLocation,
    HumanitarianRecord,
)
from app.models.query import HumanitarianQuery
from app.storage.base import RecordStorage


class SearchService:
    """
    Select broad Humanitarian Record candidates for later correlation.

    Search is the first funnel of the HCP correlation process.

    It answers only:

        "Does this record deserve to be compared in greater detail?"

    It does not:

    - confirm identity;
    - calculate the final compatibility percentage;
    - build a Humanitarian Case;
    - infer why a subject moved between locations;
    - treat event type as identity evidence;
    - permanently exclude strong descriptive matches only because they
      were reported in another country.

    Candidate discovery considers:

    1. declared geographic context;
    2. temporal context;
    3. descriptive evidence:
       name, age and recognition features.

    Structured location is preferred. Legacy free-text location remains
    supported during the schema 0.5 to 0.6 migration.
    """

    # ------------------------------------------------------------------
    # Name thresholds
    # ------------------------------------------------------------------

    NAME_STRONG_THRESHOLD = 0.72
    NAME_COMPATIBLE_THRESHOLD = 0.48

    # ------------------------------------------------------------------
    # Recognition-feature thresholds
    # ------------------------------------------------------------------

    FEATURES_STRONG_THRESHOLD = 0.42
    FEATURES_COMPATIBLE_THRESHOLD = 0.24

    # ------------------------------------------------------------------
    # Animal thresholds
    # ------------------------------------------------------------------

    ANIMAL_TEXT_STRONG_THRESHOLD = 0.68
    ANIMAL_TEXT_COMPATIBLE_THRESHOLD = 0.42

    # ------------------------------------------------------------------
    # Human age rules
    # ------------------------------------------------------------------

    STRONG_AGE_TOLERANCE = 3
    BROAD_AGE_TOLERANCE = 10

    # ------------------------------------------------------------------
    # Legacy free-text location
    # ------------------------------------------------------------------

    LEGACY_LOCATION_COMPATIBLE_THRESHOLD = 0.30

    # ------------------------------------------------------------------
    # Future international expansion
    # ------------------------------------------------------------------

    DISTANT_COUNTRY_REVIEW_AFTER_DAYS = 7

    def __init__(
        self,
        storage: RecordStorage,
    ) -> None:
        self.storage = storage

    def search_records(
        self,
        query: HumanitarianQuery,
        limit: int | None = None,
    ) -> list[HumanitarianRecord]:
        """
        Return broad candidates ordered by practical search relevance.

        Ranking order:

        1. spatial proximity according to the declared hierarchy;
        2. descriptive compatibility;
        3. temporal relevance;
        4. most recent observation.

        A strong descriptive candidate reported in another country is not
        automatically discarded. It is retained with lower priority so a
        later stage can classify it as a distant case for human review.

        Args:
            query:
                Humanitarian Query containing the information known by the
                person searching.

            limit:
                Optional maximum number of records returned.

        Raises:
            QueryProcessingError:
                If the limit is invalid or storage/search processing fails.
        """
        if limit is not None and limit < 1:
            raise QueryProcessingError(
                "search limit must be greater than or equal to 1"
            )

        try:
            records = self.storage.list_all()

            assessments = [
                assessment
                for record in records
                if (
                    assessment := self._assess_candidate(
                        query=query,
                        record=record,
                    )
                )
                is not None
            ]

            assessments.sort(
                key=self._candidate_sort_key,
            )

            ordered_records = [
                assessment.record
                for assessment in assessments
            ]

            if limit is not None:
                return ordered_records[:limit]

            return ordered_records

        except QueryProcessingError:
            raise

        except Exception as exc:
            raise QueryProcessingError(
                "Unable to process the local Humanitarian Record search"
            ) from exc

    def _assess_candidate(
        self,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> "CandidateAssessment | None":
        """
        Decide whether a record enters correlation and calculate ranking data.

        Subject type is the only unconditional exclusion rule.

        Event type is intentionally ignored because different event types may
        describe successive moments of the same humanitarian story.
        """
        if record.subject.type != query.subject.type:
            return None

        descriptive_evidence = self._descriptive_evidence(
            query=query,
            record=record,
        )

        supplied_descriptive_count = sum(
            1
            for evidence in descriptive_evidence
            if evidence.was_requested
        )

        compared_descriptive = [
            evidence
            for evidence in descriptive_evidence
            if evidence.was_compared
        ]

        strong_descriptive = [
            evidence
            for evidence in compared_descriptive
            if evidence.is_strong
        ]

        compatible_descriptive = [
            evidence
            for evidence in compared_descriptive
            if evidence.is_compatible
        ]

        spatial_assessment = self._assess_spatial_context(
            query=query,
            record=record,
        )

        should_enter_correlation = self._should_enter_correlation(
            supplied_descriptive_count=supplied_descriptive_count,
            compared_descriptive=compared_descriptive,
            strong_descriptive=strong_descriptive,
            compatible_descriptive=compatible_descriptive,
            spatial_assessment=spatial_assessment,
        )

        if not should_enter_correlation:
            return None

        descriptive_score = self._aggregate_descriptive_score(
            compared_descriptive
        )

        searched_at = query.searched_at()

        elapsed_seconds = self._elapsed_seconds(
            searched_at=searched_at,
            observed_at=record.observation.observed_at,
        )

        return CandidateAssessment(
            record=record,
            spatial_rank=spatial_assessment.rank,
            spatial_status=spatial_assessment.status,
            descriptive_score=descriptive_score,
            strong_signal_count=len(strong_descriptive),
            compatible_signal_count=len(compatible_descriptive),
            elapsed_seconds=elapsed_seconds,
        )

    @staticmethod
    def _should_enter_correlation(
        supplied_descriptive_count: int,
        compared_descriptive: list["CandidateEvidence"],
        strong_descriptive: list["CandidateEvidence"],
        compatible_descriptive: list["CandidateEvidence"],
        spatial_assessment: "SpatialAssessment",
    ) -> bool:
        """
        Apply permissive candidate-admission rules.

        Strong descriptive evidence always preserves a candidate, including
        geographically distant records.

        Weak evidence normally requires more than one compatible signal or
        useful spatial context.
        """
        if strong_descriptive:
            return True

        if len(compatible_descriptive) >= 2:
            return True

        if (
            supplied_descriptive_count == 1
            and compatible_descriptive
        ):
            return True

        if (
            compatible_descriptive
            and spatial_assessment.is_locally_relevant
        ):
            return True

        if supplied_descriptive_count == 0:
            return spatial_assessment.is_compatible

        if (
            supplied_descriptive_count > 0
            and not compared_descriptive
        ):
            return spatial_assessment.is_locally_relevant

        return False

    def _descriptive_evidence(
        self,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> list["CandidateEvidence"]:
        """
        Compare the descriptive information supplied by the Query.

        Description includes:

        - name;
        - estimated age;
        - recognition features;
        - species, breed and size for animals.

        Missing record information is unavailable, not conflicting.
        """
        evidence: list[CandidateEvidence] = []

        if query.subject.reported_label is not None:
            evidence.append(
                self._name_evidence(
                    query_value=query.subject.reported_label,
                    record_value=record.subject.reported_label,
                )
            )

        if query.subject.estimated_age is not None:
            evidence.append(
                self._age_evidence(
                    query_age=query.subject.estimated_age,
                    record_age=record.subject.estimated_age,
                )
            )

        if query.subject.recognition_features is not None:
            evidence.append(
                self._features_evidence(
                    query_value=query.subject.recognition_features,
                    record_value=record.subject.recognition_features,
                )
            )

        if query.subject.type == "animal":
            if query.subject.species is not None:
                evidence.append(
                    self._animal_text_evidence(
                        field="species",
                        query_value=query.subject.species,
                        record_value=getattr(
                            record.subject,
                            "species",
                            None,
                        ),
                    )
                )

            if query.subject.breed is not None:
                evidence.append(
                    self._animal_text_evidence(
                        field="breed",
                        query_value=query.subject.breed,
                        record_value=getattr(
                            record.subject,
                            "breed",
                            None,
                        ),
                    )
                )

            if query.subject.size is not None:
                evidence.append(
                    self._exact_evidence(
                        field="size",
                        query_value=query.subject.size,
                        record_value=getattr(
                            record.subject,
                            "size",
                            None,
                        ),
                    )
                )

        return evidence

    def _name_evidence(
        self,
        query_value: str,
        record_value: str | None,
    ) -> "CandidateEvidence":
        """
        Compare a human-entered name or animal name.

        The comparison tolerates:

        - additional or omitted surnames;
        - surname-order differences;
        - accents and punctuation;
        - case differences;
        - small typing variants such as Maria / Marias.
        """
        if record_value is None:
            return CandidateEvidence.unavailable(
                field="reported_label"
            )

        similarity = self._name_similarity(
            query_value,
            record_value,
        )

        return CandidateEvidence(
            field="reported_label",
            score=similarity,
            was_requested=True,
            was_compared=True,
            is_compatible=(
                similarity
                >= self.NAME_COMPATIBLE_THRESHOLD
            ),
            is_strong=(
                similarity
                >= self.NAME_STRONG_THRESHOLD
            ),
        )

    def _age_evidence(
        self,
        query_age: int,
        record_age: int | None,
    ) -> "CandidateEvidence":
        """
        Compare approximate human ages.

        A difference of plus or minus three years is a strong signal.

        A wider margin remains available during candidate discovery because
        reported ages may be estimates. Final scoring belongs to the
        correlation service.
        """
        if record_age is None:
            return CandidateEvidence.unavailable(
                field="estimated_age"
            )

        difference = abs(
            query_age - record_age
        )

        if difference <= self.STRONG_AGE_TOLERANCE:
            score = max(
                0.85,
                1.0 - difference * 0.05,
            )

            return CandidateEvidence(
                field="estimated_age",
                score=score,
                was_requested=True,
                was_compared=True,
                is_compatible=True,
                is_strong=True,
            )

        if difference <= self.BROAD_AGE_TOLERANCE:
            score = max(
                0.35,
                1.0
                - (
                    difference
                    / self.BROAD_AGE_TOLERANCE
                ),
            )

            return CandidateEvidence(
                field="estimated_age",
                score=score,
                was_requested=True,
                was_compared=True,
                is_compatible=True,
                is_strong=False,
            )

        return CandidateEvidence(
            field="estimated_age",
            score=0.0,
            was_requested=True,
            was_compared=True,
            is_compatible=False,
            is_strong=False,
        )

    def _features_evidence(
        self,
        query_value: str,
        record_value: str | None,
    ) -> "CandidateEvidence":
        """
        Compare recognition features permissively.

        Characteristics are high-value evidence because independent people
        may describe the same visible traits using different sentence
        structures or levels of detail.
        """
        if record_value is None:
            return CandidateEvidence.unavailable(
                field="recognition_features"
            )

        similarity = self._descriptive_text_similarity(
            query_value,
            record_value,
        )

        return CandidateEvidence(
            field="recognition_features",
            score=similarity,
            was_requested=True,
            was_compared=True,
            is_compatible=(
                similarity
                >= self.FEATURES_COMPATIBLE_THRESHOLD
            ),
            is_strong=(
                similarity
                >= self.FEATURES_STRONG_THRESHOLD
            ),
        )

    def _animal_text_evidence(
        self,
        field: str,
        query_value: str,
        record_value: str | None,
    ) -> "CandidateEvidence":
        """
        Compare species or breed.
        """
        if record_value is None:
            return CandidateEvidence.unavailable(
                field=field
            )

        similarity = self._descriptive_text_similarity(
            query_value,
            record_value,
        )

        return CandidateEvidence(
            field=field,
            score=similarity,
            was_requested=True,
            was_compared=True,
            is_compatible=(
                similarity
                >= self.ANIMAL_TEXT_COMPATIBLE_THRESHOLD
            ),
            is_strong=(
                similarity
                >= self.ANIMAL_TEXT_STRONG_THRESHOLD
            ),
        )

    def _exact_evidence(
        self,
        field: str,
        query_value: object,
        record_value: object | None,
    ) -> "CandidateEvidence":
        """
        Compare a categorical animal field such as size.
        """
        if record_value is None:
            return CandidateEvidence.unavailable(
                field=field
            )

        query_normalized = self._normalize_text(
            str(query_value)
        )

        record_normalized = self._normalize_text(
            str(record_value)
        )

        is_equal = bool(
            query_normalized
            and query_normalized
            == record_normalized
        )

        return CandidateEvidence(
            field=field,
            score=1.0 if is_equal else 0.0,
            was_requested=True,
            was_compared=True,
            is_compatible=is_equal,
            is_strong=is_equal,
        )

    def _assess_spatial_context(
        self,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> "SpatialAssessment":
        """
        Classify spatial compatibility using structured location first.

        Spatial ranking:

        0. same district;
        1. same locality;
        2. same second administrative level;
        3. same first administrative level;
        4. same country;
        5. different country after sufficient elapsed time;
        6. different country with little elapsed time;
        7. legacy free-text compatibility;
        8. location unavailable.

        A distant record remains available when descriptive evidence is
        strong. Later sprints will expose distant cases separately.
        """
        query_location = query.declared_location()

        record_location = (
            record.observation.declared_location
        )

        if (
            query_location is not None
            and record_location is not None
        ):
            return self._compare_structured_locations(
                query_location=query_location,
                record_location=record_location,
                searched_at=query.searched_at(),
                observed_at=record.observation.observed_at,
            )

        query_legacy_location = (
            query.observation.reported_location
            if query.observation is not None
            else None
        )

        record_legacy_location = (
            record.observation.reported_location
        )

        if (
            query_legacy_location is not None
            and record_legacy_location is not None
        ):
            similarity = (
                self._descriptive_text_similarity(
                    query_legacy_location,
                    record_legacy_location,
                )
            )

            return SpatialAssessment(
                rank=7,
                status="legacy_location",
                similarity=similarity,
                is_compatible=(
                    similarity
                    >= self.LEGACY_LOCATION_COMPATIBLE_THRESHOLD
                ),
                is_locally_relevant=(
                    similarity >= 0.50
                ),
            )

        return SpatialAssessment(
            rank=8,
            status="location_unavailable",
            similarity=0.0,
            is_compatible=False,
            is_locally_relevant=False,
        )

    def _compare_structured_locations(
        self,
        query_location: DeclaredLocation,
        record_location: DeclaredLocation,
        searched_at: datetime | None,
        observed_at: datetime,
    ) -> "SpatialAssessment":
        """
        Compare the declared geographic hierarchy.

        Country differences are not treated as permanent exclusion because
        enough elapsed time may permit displacement.

        Without coordinates, HCP classifies hierarchy rather than calculating
        physical distance.
        """
        if (
            query_location.country_code
            != record_location.country_code
        ):
            elapsed_days = (
                self._elapsed_seconds(
                    searched_at=searched_at,
                    observed_at=observed_at,
                )
                / 86_400
            )

            if (
                elapsed_days
                >= self.DISTANT_COUNTRY_REVIEW_AFTER_DAYS
            ):
                return SpatialAssessment(
                    rank=5,
                    status="different_country_distant_review",
                    similarity=0.0,
                    is_compatible=True,
                    is_locally_relevant=False,
                )

            return SpatialAssessment(
                rank=6,
                status="different_country_recent",
                similarity=0.0,
                is_compatible=False,
                is_locally_relevant=False,
            )

        admin_level_1_similarity = (
            self._location_level_similarity(
                query_location.admin_level_1,
                record_location.admin_level_1,
            )
        )

        admin_level_2_similarity = (
            self._optional_location_level_similarity(
                query_location.admin_level_2,
                record_location.admin_level_2,
            )
        )

        locality_similarity = (
            self._location_level_similarity(
                query_location.locality,
                record_location.locality,
            )
        )

        district_similarity = (
            self._optional_location_level_similarity(
                query_location.district,
                record_location.district,
            )
        )

        if (
            district_similarity is not None
            and district_similarity >= 0.82
            and locality_similarity >= 0.82
        ):
            return SpatialAssessment(
                rank=0,
                status="same_district",
                similarity=district_similarity,
                is_compatible=True,
                is_locally_relevant=True,
            )

        if locality_similarity >= 0.82:
            return SpatialAssessment(
                rank=1,
                status="same_locality",
                similarity=locality_similarity,
                is_compatible=True,
                is_locally_relevant=True,
            )

        if (
            admin_level_2_similarity is not None
            and admin_level_2_similarity >= 0.82
        ):
            return SpatialAssessment(
                rank=2,
                status="same_admin_level_2",
                similarity=admin_level_2_similarity,
                is_compatible=True,
                is_locally_relevant=True,
            )

        if admin_level_1_similarity >= 0.82:
            return SpatialAssessment(
                rank=3,
                status="same_admin_level_1",
                similarity=admin_level_1_similarity,
                is_compatible=True,
                is_locally_relevant=True,
            )

        return SpatialAssessment(
            rank=4,
            status="same_country",
            similarity=1.0,
            is_compatible=True,
            is_locally_relevant=False,
        )

    @classmethod
    def _location_level_similarity(
        cls,
        first_value: str,
        second_value: str,
    ) -> float:
        """
        Compare a required geographic hierarchy level.
        """
        return cls._descriptive_text_similarity(
            first_value,
            second_value,
        )

    @classmethod
    def _optional_location_level_similarity(
        cls,
        first_value: str | None,
        second_value: str | None,
    ) -> float | None:
        """
        Compare an optional hierarchy level.

        None means the level was unavailable and must not be interpreted as a
        contradiction.
        """
        if (
            first_value is None
            or second_value is None
        ):
            return None

        return cls._descriptive_text_similarity(
            first_value,
            second_value,
        )

    @staticmethod
    def _aggregate_descriptive_score(
        evidence: list["CandidateEvidence"],
    ) -> float:
        """
        Calculate a lightweight ranking score.

        This score is internal to SearchService and is not the final HCP
        compatibility percentage.
        """
        compared = [
            item
            for item in evidence
            if item.was_compared
        ]

        if not compared:
            return 0.0

        return round(
            sum(
                item.score
                for item in compared
            )
            / len(compared),
            4,
        )

    @staticmethod
    def _candidate_sort_key(
        assessment: "CandidateAssessment",
    ) -> tuple[
        int,
        float,
        int,
        int,
        float,
    ]:
        """
        Sort candidates from most useful to least useful.

        Lower spatial rank is better. Remaining values are descending, so
        they are negated.
        """
        observed_timestamp = (
            assessment.record
            .observation
            .observed_at
            .timestamp()
        )

        return (
            assessment.spatial_rank,
            -assessment.descriptive_score,
            -assessment.strong_signal_count,
            -assessment.compatible_signal_count,
            -observed_timestamp,
        )

    @staticmethod
    def _elapsed_seconds(
        searched_at: datetime | None,
        observed_at: datetime,
    ) -> float:
        """
        Return non-negative elapsed seconds between report and search.

        When the Query has no search timestamp, zero is returned to preserve
        compatibility with legacy clients.
        """
        if searched_at is None:
            return 0.0

        return max(
            0.0,
            (
                searched_at
                - observed_at
            ).total_seconds(),
        )

    # ------------------------------------------------------------------
    # Text similarity
    # ------------------------------------------------------------------

    @classmethod
    def _name_similarity(
        cls,
        first_value: str,
        second_value: str,
    ) -> float:
        """
        Compare human-entered names using several deterministic signals.
        """
        first_normalized = cls._normalize_text(
            first_value
        )

        second_normalized = cls._normalize_text(
            second_value
        )

        if (
            not first_normalized
            or not second_normalized
        ):
            return 0.0

        if first_normalized == second_normalized:
            return 1.0

        first_tokens = first_normalized.split()
        second_tokens = second_normalized.split()

        return round(
            max(
                SequenceMatcher(
                    None,
                    first_normalized,
                    second_normalized,
                ).ratio(),
                cls._containment_score(
                    first_normalized,
                    second_normalized,
                ),
                cls._token_overlap_score(
                    first_tokens,
                    second_tokens,
                ),
                cls._fuzzy_token_overlap_score(
                    first_tokens,
                    second_tokens,
                ),
                cls._name_core_score(
                    first_tokens,
                    second_tokens,
                ),
            ),
            4,
        )

    @classmethod
    def _descriptive_text_similarity(
        cls,
        first_value: str,
        second_value: str,
    ) -> float:
        """
        Compare descriptions while tolerating different sentence lengths,
        word order and small typing variants.
        """
        first_normalized = cls._normalize_text(
            first_value
        )

        second_normalized = cls._normalize_text(
            second_value
        )

        if (
            not first_normalized
            or not second_normalized
        ):
            return 0.0

        if first_normalized == second_normalized:
            return 1.0

        first_tokens = first_normalized.split()
        second_tokens = second_normalized.split()

        return round(
            max(
                SequenceMatcher(
                    None,
                    first_normalized,
                    second_normalized,
                ).ratio(),
                cls._containment_score(
                    first_normalized,
                    second_normalized,
                ),
                cls._token_overlap_score(
                    first_tokens,
                    second_tokens,
                ),
                cls._fuzzy_token_overlap_score(
                    first_tokens,
                    second_tokens,
                ),
                cls._shorter_text_coverage(
                    first_tokens,
                    second_tokens,
                ),
            ),
            4,
        )

    @staticmethod
    def _containment_score(
        first_value: str,
        second_value: str,
    ) -> float:
        """
        Score direct normalized containment.
        """
        if (
            first_value not in second_value
            and second_value not in first_value
        ):
            return 0.0

        shortest_length = min(
            len(first_value),
            len(second_value),
        )

        longest_length = max(
            len(first_value),
            len(second_value),
        )

        if longest_length == 0:
            return 0.0

        return max(
            shortest_length / longest_length,
            0.75,
        )

    @staticmethod
    def _token_overlap_score(
        first_tokens: list[str],
        second_tokens: list[str],
    ) -> float:
        """
        Measure exact token coverage using the shorter side.
        """
        first_set = set(first_tokens)
        second_set = set(second_tokens)

        if not first_set or not second_set:
            return 0.0

        denominator = min(
            len(first_set),
            len(second_set),
        )

        return (
            len(
                first_set.intersection(
                    second_set
                )
            )
            / denominator
        )

    @staticmethod
    def _fuzzy_token_overlap_score(
        first_tokens: list[str],
        second_tokens: list[str],
    ) -> float:
        """
        Match tokens while tolerating minor typing differences.
        """
        if not first_tokens or not second_tokens:
            return 0.0

        shorter = (
            first_tokens
            if len(first_tokens)
            <= len(second_tokens)
            else second_tokens
        )

        longer = (
            second_tokens
            if shorter is first_tokens
            else first_tokens
        )

        matched_scores: list[float] = []

        for token in shorter:
            best_score = max(
                SequenceMatcher(
                    None,
                    token,
                    candidate,
                ).ratio()
                for candidate in longer
            )

            if best_score >= 0.72:
                matched_scores.append(
                    best_score
                )

        if not matched_scores:
            return 0.0

        coverage = (
            len(matched_scores)
            / len(shorter)
        )

        average_similarity = (
            sum(matched_scores)
            / len(matched_scores)
        )

        return (
            coverage
            * average_similarity
        )

    @classmethod
    def _name_core_score(
        cls,
        first_tokens: list[str],
        second_tokens: list[str],
    ) -> float:
        """
        Compare the first known name and available surnames.
        """
        if not first_tokens or not second_tokens:
            return 0.0

        first_name_similarity = SequenceMatcher(
            None,
            first_tokens[0],
            second_tokens[0],
        ).ratio()

        if first_name_similarity < 0.72:
            return 0.0

        first_surnames = first_tokens[1:]
        second_surnames = second_tokens[1:]

        if (
            not first_surnames
            or not second_surnames
        ):
            return (
                first_name_similarity
                * 0.75
            )

        surname_similarity = (
            cls._fuzzy_token_overlap_score(
                first_surnames,
                second_surnames,
            )
        )

        return (
            first_name_similarity * 0.55
            + surname_similarity * 0.45
        )

    @staticmethod
    def _shorter_text_coverage(
        first_tokens: list[str],
        second_tokens: list[str],
    ) -> float:
        """
        Measure how much of the shorter description appears in the longer.
        """
        if not first_tokens or not second_tokens:
            return 0.0

        shorter = (
            first_tokens
            if len(first_tokens)
            <= len(second_tokens)
            else second_tokens
        )

        longer = (
            second_tokens
            if shorter is first_tokens
            else first_tokens
        )

        covered_score = 0.0

        for token in shorter:
            best_similarity = max(
                SequenceMatcher(
                    None,
                    token,
                    candidate,
                ).ratio()
                for candidate in longer
            )

            if best_similarity >= 0.72:
                covered_score += (
                    best_similarity
                )

        return (
            covered_score
            / len(shorter)
        )

    @staticmethod
    def _normalize_text(
        value: str,
    ) -> str:
        """
        Normalize human-entered text before deterministic comparison.
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


@dataclass(frozen=True)
class CandidateEvidence:
    """
    Internal descriptive evidence used only for candidate discovery.
    """

    field: str
    score: float
    was_requested: bool
    was_compared: bool
    is_compatible: bool
    is_strong: bool

    @classmethod
    def unavailable(
        cls,
        field: str,
    ) -> "CandidateEvidence":
        """
        Represent evidence requested by the Query but absent in the record.
        """
        return cls(
            field=field,
            score=0.0,
            was_requested=True,
            was_compared=False,
            is_compatible=False,
            is_strong=False,
        )


@dataclass(frozen=True)
class SpatialAssessment:
    """
    Internal spatial classification.

    This is not yet part of the canonical HCP response. Sprint 2 will expose
    space-time plausibility through correlation results and related cases.
    """

    rank: int
    status: str
    similarity: float
    is_compatible: bool
    is_locally_relevant: bool


@dataclass(frozen=True)
class CandidateAssessment:
    """
    Internal ranking information for one candidate record.
    """

    record: HumanitarianRecord
    spatial_rank: int
    spatial_status: str
    descriptive_score: float
    strong_signal_count: int
    compatible_signal_count: int
    elapsed_seconds: float
