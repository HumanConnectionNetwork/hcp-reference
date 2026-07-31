import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Callable

from app.core.errors import CorrelationProcessingError
from app.models.correlation import (
    CorrelationResult,
    CorrelationSignal,
    CorrelationSignalStatus,
    confidence_from_score,
)
from app.models.humanitarian_record import (
    DeclaredLocation,
    HumanitarianRecord,
)
from app.models.query import HumanitarianQuery


class CorrelationService:
    """
    Build an explainable compatibility assessment between one search context
    and one Humanitarian Record.

    HCP correlation is based on three groups of evidence:

    1. Space
       Geographic context declared by the reporting person and by the person
       performing the search.

    2. Time
       The moment of the report, the moment of the search and the elapsed time
       available for a plausible displacement.

    3. Description
       Name, estimated age, recognition features and animal-specific data.

    The service does not:

    - confirm identity;
    - treat event type as identity evidence;
    - create Humanitarian Cases;
    - infer why a person or animal moved;
    - use the reporter type as compatibility evidence;
    - permanently discard a distant report merely because it belongs to
      another country.

    A high score means that the compared information is compatible. It does
    not mean that identity has been established.
    """

    # ------------------------------------------------------------------
    # Human evidence weights
    # ------------------------------------------------------------------

    HUMAN_SPATIAL_WEIGHT = 30.0
    HUMAN_TEMPORAL_WEIGHT = 15.0
    HUMAN_NAME_WEIGHT = 20.0
    HUMAN_AGE_WEIGHT = 10.0
    HUMAN_FEATURES_WEIGHT = 25.0

    # ------------------------------------------------------------------
    # Animal evidence weights
    # ------------------------------------------------------------------

    ANIMAL_SPATIAL_WEIGHT = 30.0
    ANIMAL_TEMPORAL_WEIGHT = 15.0
    ANIMAL_NAME_WEIGHT = 15.0
    ANIMAL_SPECIES_WEIGHT = 10.0
    ANIMAL_BREED_WEIGHT = 5.0
    ANIMAL_SIZE_WEIGHT = 5.0
    ANIMAL_FEATURES_WEIGHT = 20.0

    # ------------------------------------------------------------------
    # Text thresholds
    # ------------------------------------------------------------------

    NAME_MATCH_THRESHOLD = 0.85
    NAME_PARTIAL_THRESHOLD = 0.48

    FEATURES_MATCH_THRESHOLD = 0.76
    FEATURES_PARTIAL_THRESHOLD = 0.24

    ANIMAL_TEXT_MATCH_THRESHOLD = 0.85
    ANIMAL_TEXT_PARTIAL_THRESHOLD = 0.42

    LEGACY_LOCATION_MATCH_THRESHOLD = 0.80
    LEGACY_LOCATION_PARTIAL_THRESHOLD = 0.30

    # ------------------------------------------------------------------
    # Age rules
    # ------------------------------------------------------------------

    STRONG_AGE_TOLERANCE = 3
    BROAD_AGE_TOLERANCE = 10

    # ------------------------------------------------------------------
    # Space-time rules
    # ------------------------------------------------------------------

    DISTANT_COUNTRY_REVIEW_AFTER_DAYS = 7
    DISTANT_COUNTRY_BROAD_AFTER_DAYS = 30

    def correlate_records(
        self,
        query: HumanitarianQuery,
        records: list[HumanitarianRecord],
        limit: int | None = None,
        minimum_score: float = 0.0,
    ) -> list[CorrelationResult]:
        """
        Correlate one Query against candidate Humanitarian Records.

        SearchService decides which records deserve comparison.
        CorrelationService explains how compatible each candidate is.

        Results are ordered by:

        1. final compatibility score;
        2. evidence strength;
        3. recency of the source record.
        """
        if limit is not None and limit < 1:
            raise CorrelationProcessingError(
                "correlation limit must be greater than or equal to 1"
            )

        if not 0.0 <= minimum_score <= 100.0:
            raise CorrelationProcessingError(
                "minimum_score must be between 0 and 100"
            )

        try:
            assessed_results = [
                (
                    self.correlate_record(
                        query=query,
                        record=record,
                    ),
                    record,
                )
                for record in records
                if record.subject.type == query.subject.type
            ]

            filtered_results = [
                (result, record)
                for result, record in assessed_results
                if result.score >= minimum_score
            ]

            filtered_results.sort(
                key=lambda item: (
                    -item[0].score,
                    -self._confidence_sort_value(
                        item[0].confidence.value
                    ),
                    -item[1].observation.observed_at.timestamp(),
                )
            )

            results = [
                result
                for result, _record in filtered_results
            ]

            if limit is not None:
                return results[:limit]

            return results

        except CorrelationProcessingError:
            raise

        except Exception as exc:
            raise CorrelationProcessingError(
                "Unable to complete local Humanitarian Record correlation"
            ) from exc

    def correlate_record(
        self,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> CorrelationResult:
        """
        Correlate one search context with one Humanitarian Record.

        Only information supplied by the Query participates.

        Missing evidence is reported as NOT_AVAILABLE. It does not become a
        contradiction and is excluded from the compatibility denominator.

        The evidence level is calculated independently from compatibility.
        Therefore:

        - exact name alone can have high compatibility but low evidence;
        - matching space, time, name, age and characteristics can have both
          high compatibility and high evidence.
        """
        if query.subject.type != record.subject.type:
            raise CorrelationProcessingError(
                "Query and Humanitarian Record subject types must match"
            )

        signals: list[CorrelationSignal] = []

        self._append_spatial_signal(
            signals=signals,
            query=query,
            record=record,
        )

        self._append_temporal_signal(
            signals=signals,
            query=query,
            record=record,
        )

        self._append_name_signal(
            signals=signals,
            query_value=query.subject.reported_label,
            record_value=record.subject.reported_label,
            weight=self._name_weight(
                query.subject.type
            ),
        )

        if query.subject.type == "human":
            self._append_age_signal(
                signals=signals,
                query_age=query.subject.estimated_age,
                record_age=record.subject.estimated_age,
                weight=self.HUMAN_AGE_WEIGHT,
            )

        self._append_features_signal(
            signals=signals,
            query_value=query.subject.recognition_features,
            record_value=record.subject.recognition_features,
            weight=self._features_weight(
                query.subject.type
            ),
        )

        if query.subject.type == "animal":
            self._append_animal_signals(
                signals=signals,
                query=query,
                record=record,
            )

        compatibility_score = (
            self._calculate_compatibility_score(
                signals=signals,
                subject_type=query.subject.type,
            )
        )

        evidence_strength = (
            self._calculate_evidence_strength(
                signals=signals,
                subject_type=query.subject.type,
            )
        )

        return CorrelationResult(
            record_id=record.id,
            subject_type=record.subject.type,
            score=compatibility_score,
            confidence=confidence_from_score(
                evidence_strength
            ),
            signals=signals,
        )

    # ------------------------------------------------------------------
    # Spatial evidence
    # ------------------------------------------------------------------

    def _append_spatial_signal(
        self,
        signals: list[CorrelationSignal],
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> None:
        """
        Compare declared geographic context.

        Structured location is preferred.

        The hierarchy is interpreted as:

        country
        → admin_level_1
        → admin_level_2
        → locality
        → district

        A different country is a strong spatial difference, but it is not a
        permanent exclusion. Elapsed time is considered so distant reports
        can remain visible for later human review.
        """
        query_location = query.declared_location()

        record_location = (
            record.observation.declared_location
        )

        weight = self._spatial_weight(
            query.subject.type
        )

        if (
            query_location is not None
            and record_location is not None
        ):
            self._append_structured_spatial_signal(
                signals=signals,
                query_location=query_location,
                record_location=record_location,
                searched_at=query.searched_at(),
                observed_at=record.observation.observed_at,
                weight=weight,
            )

            return

        query_legacy_location = (
            query.observation.reported_location
            if query.observation is not None
            else None
        )

        record_legacy_location = (
            record.observation.reported_location
        )

        if query_legacy_location is None:
            return

        if record_legacy_location is None:
            signals.append(
                CorrelationSignal(
                    field="observation.declared_location",
                    status=CorrelationSignalStatus.NOT_AVAILABLE,
                    contribution=0.0,
                    explanation=(
                        "The candidate record does not contain geographic "
                        "evidence that can be compared with the search."
                    ),
                    query_value=query_legacy_location,
                    record_value=None,
                )
            )

            return

        similarity = self._descriptive_text_similarity(
            query_legacy_location,
            record_legacy_location,
        )

        status = self._similarity_status(
            similarity=similarity,
            match_threshold=(
                self.LEGACY_LOCATION_MATCH_THRESHOLD
            ),
            partial_threshold=(
                self.LEGACY_LOCATION_PARTIAL_THRESHOLD
            ),
        )

        contribution = (
            self._contribution_for_similarity(
                similarity=similarity,
                status=status,
                weight=weight,
            )
        )

        signals.append(
            CorrelationSignal(
                field="observation.reported_location",
                status=status,
                contribution=contribution,
                explanation=self._legacy_location_explanation(
                    similarity=similarity,
                    status=status,
                ),
                query_value=query_legacy_location,
                record_value=record_legacy_location,
            )
        )

    def _append_structured_spatial_signal(
        self,
        signals: list[CorrelationSignal],
        query_location: DeclaredLocation,
        record_location: DeclaredLocation,
        searched_at: datetime | None,
        observed_at: datetime,
        weight: float,
    ) -> None:
        """
        Build one explainable signal from the structured hierarchy.
        """
        comparison = self._compare_structured_locations(
            query_location=query_location,
            record_location=record_location,
            searched_at=searched_at,
            observed_at=observed_at,
        )

        contribution = (
            self._contribution_for_similarity(
                similarity=comparison.similarity,
                status=comparison.status,
                weight=weight,
            )
        )

        signals.append(
            CorrelationSignal(
                field="observation.declared_location",
                status=comparison.status,
                contribution=contribution,
                explanation=comparison.explanation,
                query_value=(
                    query_location.to_display_text()
                ),
                record_value=(
                    record_location.to_display_text()
                ),
            )
        )

    def _compare_structured_locations(
        self,
        query_location: DeclaredLocation,
        record_location: DeclaredLocation,
        searched_at: datetime | None,
        observed_at: datetime,
    ) -> "SpatialComparison":
        """
        Compare location hierarchy without requiring coordinates.

        Different-country compatibility is deliberately time-sensitive.

        This first spatial model does not calculate kilometers. Future
        versions may use local geographic datasets while preserving the same
        DeclaredLocation contract.
        """
        if (
            query_location.country_code
            != record_location.country_code
        ):
            elapsed_days = self._elapsed_days(
                searched_at=searched_at,
                observed_at=observed_at,
            )

            if (
                elapsed_days
                >= self.DISTANT_COUNTRY_BROAD_AFTER_DAYS
            ):
                return SpatialComparison(
                    status=(
                        CorrelationSignalStatus.PARTIAL_MATCH
                    ),
                    similarity=0.45,
                    explanation=(
                        "The search and report refer to different countries, "
                        f"but {self._format_days(elapsed_days)} have elapsed. "
                        "The geographic difference is preserved as a distant "
                        "case for human review rather than treated as a "
                        "permanent exclusion."
                    ),
                )

            if (
                elapsed_days
                >= self.DISTANT_COUNTRY_REVIEW_AFTER_DAYS
            ):
                return SpatialComparison(
                    status=(
                        CorrelationSignalStatus.PARTIAL_MATCH
                    ),
                    similarity=0.25,
                    explanation=(
                        "The search and report refer to different countries. "
                        f"{self._format_days(elapsed_days)} have elapsed, so "
                        "the report may remain available as a distant case "
                        "for human review."
                    ),
                )

            return SpatialComparison(
                status=CorrelationSignalStatus.CONFLICT,
                similarity=0.0,
                explanation=(
                    "The search and report refer to different countries and "
                    "the elapsed time is short. The geographic contexts are "
                    "not currently considered locally compatible."
                ),
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
            return SpatialComparison(
                status=CorrelationSignalStatus.MATCH,
                similarity=1.0,
                explanation=(
                    "The declared locations are compatible at country, "
                    "locality and district level."
                ),
            )

        if locality_similarity >= 0.82:
            return SpatialComparison(
                status=CorrelationSignalStatus.MATCH,
                similarity=0.90,
                explanation=(
                    "The declared locations are compatible in the same "
                    "locality."
                ),
            )

        if (
            admin_level_2_similarity is not None
            and admin_level_2_similarity >= 0.82
        ):
            return SpatialComparison(
                status=(
                    CorrelationSignalStatus.PARTIAL_MATCH
                ),
                similarity=0.76,
                explanation=(
                    "The declared locations are compatible at the second "
                    "administrative level, but the locality or district is "
                    "different or unavailable."
                ),
            )

        if admin_level_1_similarity >= 0.82:
            return SpatialComparison(
                status=(
                    CorrelationSignalStatus.PARTIAL_MATCH
                ),
                similarity=0.60,
                explanation=(
                    "The declared locations belong to the same first "
                    "administrative region."
                ),
            )

        return SpatialComparison(
            status=CorrelationSignalStatus.PARTIAL_MATCH,
            similarity=0.40,
            explanation=(
                "The declared locations belong to the same country but to "
                "different or insufficiently similar regions."
            ),
        )

    # ------------------------------------------------------------------
    # Temporal evidence
    # ------------------------------------------------------------------

    def _append_temporal_signal(
        self,
        signals: list[CorrelationSignal],
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> None:
        """
        Compare the report time with the search time.

        The search timestamp represents when the search context was declared.

        Time is not interpreted alone. When countries differ, the spatial
        signal already uses elapsed time to decide whether a distant report
        should remain available for review.
        """
        searched_at = query.searched_at()

        if searched_at is None:
            return

        observed_at = (
            record.observation.observed_at
        )

        elapsed_seconds = (
            searched_at - observed_at
        ).total_seconds()

        weight = self._temporal_weight(
            query.subject.type
        )

        if elapsed_seconds < 0:
            signals.append(
                CorrelationSignal(
                    field="observation.search_time",
                    status=CorrelationSignalStatus.CONFLICT,
                    contribution=0.0,
                    explanation=(
                        "The report timestamp occurs after the declared "
                        "search timestamp, so the temporal sequence is not "
                        "currently plausible."
                    ),
                    query_value=searched_at.isoformat(),
                    record_value=observed_at.isoformat(),
                )
            )

            return

        elapsed_days = (
            elapsed_seconds / 86_400
        )

        status, similarity, explanation = (
            self._temporal_interpretation(
                elapsed_days
            )
        )

        contribution = (
            self._contribution_for_similarity(
                similarity=similarity,
                status=status,
                weight=weight,
            )
        )

        signals.append(
            CorrelationSignal(
                field="observation.search_time",
                status=status,
                contribution=contribution,
                explanation=explanation,
                query_value=searched_at.isoformat(),
                record_value=observed_at.isoformat(),
            )
        )

    @staticmethod
    def _temporal_interpretation(
        elapsed_days: float,
    ) -> tuple[
        CorrelationSignalStatus,
        float,
        str,
    ]:
        """
        Interpret elapsed time without assuming a fixed travel speed.

        Older reports remain useful, but temporal proximity contributes more
        strongly to the immediate local search.
        """
        if elapsed_days <= 1:
            return (
                CorrelationSignalStatus.MATCH,
                1.0,
                (
                    "The report and search occurred within approximately "
                    "one day, providing strong temporal continuity."
                ),
            )

        if elapsed_days <= 7:
            return (
                CorrelationSignalStatus.MATCH,
                0.90,
                (
                    "The report occurred within the previous week and "
                    "remains strongly relevant to the search."
                ),
            )

        if elapsed_days <= 30:
            return (
                CorrelationSignalStatus.PARTIAL_MATCH,
                0.75,
                (
                    "The report occurred within the previous month and "
                    "remains temporally compatible."
                ),
            )

        if elapsed_days <= 180:
            return (
                CorrelationSignalStatus.PARTIAL_MATCH,
                0.55,
                (
                    "Several weeks or months separate the report and search. "
                    "The report may still be useful as historical context."
                ),
            )

        return (
            CorrelationSignalStatus.PARTIAL_MATCH,
            0.35,
            (
                "The report is old in relation to the search, but it remains "
                "available as historical humanitarian information."
            ),
        )

    # ------------------------------------------------------------------
    # Descriptive evidence
    # ------------------------------------------------------------------

    def _append_name_signal(
        self,
        signals: list[CorrelationSignal],
        query_value: str | None,
        record_value: str | None,
        weight: float,
    ) -> None:
        """
        Compare the name supplied by the person searching.

        The public concept is simply 'name'. Internally the comparison
        tolerates:

        - additional or omitted surnames;
        - different surname order;
        - accents and punctuation;
        - small typing variations.
        """
        self._append_text_signal(
            signals=signals,
            field="subject.reported_label",
            query_value=query_value,
            record_value=record_value,
            weight=weight,
            description="name",
            match_threshold=self.NAME_MATCH_THRESHOLD,
            partial_threshold=self.NAME_PARTIAL_THRESHOLD,
            similarity_function=self._name_similarity,
        )

    def _append_features_signal(
        self,
        signals: list[CorrelationSignal],
        query_value: str | None,
        record_value: str | None,
        weight: float,
    ) -> None:
        """
        Compare recognition characteristics as high-value evidence.
        """
        self._append_text_signal(
            signals=signals,
            field="subject.recognition_features",
            query_value=query_value,
            record_value=record_value,
            weight=weight,
            description="recognition features",
            match_threshold=self.FEATURES_MATCH_THRESHOLD,
            partial_threshold=self.FEATURES_PARTIAL_THRESHOLD,
            similarity_function=(
                self._descriptive_text_similarity
            ),
        )

    def _append_age_signal(
        self,
        signals: list[CorrelationSignal],
        query_age: int | None,
        record_age: int | None,
        weight: float,
    ) -> None:
        """
        Compare approximate human ages.

        Rules:

        - exact age:
          complete match;

        - difference from one to three years:
          strong match;

        - difference from four to ten years:
          partial match;

        - difference greater than ten years:
          conflict.

        Age remains approximate and never establishes identity.
        """
        if query_age is None:
            return

        if record_age is None:
            signals.append(
                CorrelationSignal(
                    field="subject.estimated_age",
                    status=CorrelationSignalStatus.NOT_AVAILABLE,
                    contribution=0.0,
                    explanation=(
                        "The candidate report does not contain estimated "
                        "age information."
                    ),
                    query_value=query_age,
                    record_value=None,
                )
            )

            return

        difference = abs(
            query_age - record_age
        )

        if difference == 0:
            status = CorrelationSignalStatus.MATCH
            similarity = 1.0
            explanation = (
                "The estimated ages are equal."
            )

        elif difference <= self.STRONG_AGE_TOLERANCE:
            status = CorrelationSignalStatus.MATCH
            similarity = max(
                0.85,
                1.0 - difference * 0.05,
            )
            explanation = (
                "The estimated ages are strongly compatible, with a "
                f"difference of {difference} "
                f"year{'' if difference == 1 else 's'}."
            )

        elif difference <= self.BROAD_AGE_TOLERANCE:
            status = (
                CorrelationSignalStatus.PARTIAL_MATCH
            )
            similarity = max(
                0.35,
                1.0
                - (
                    difference
                    / self.BROAD_AGE_TOLERANCE
                ),
            )
            explanation = (
                "The estimated ages are partially compatible, with a "
                f"difference of {difference} years."
            )

        else:
            status = CorrelationSignalStatus.CONFLICT
            similarity = 0.0
            explanation = (
                "The estimated ages differ by more than ten years."
            )

        contribution = (
            self._contribution_for_similarity(
                similarity=similarity,
                status=status,
                weight=weight,
            )
        )

        signals.append(
            CorrelationSignal(
                field="subject.estimated_age",
                status=status,
                contribution=contribution,
                explanation=explanation,
                query_value=query_age,
                record_value=record_age,
            )
        )

    def _append_animal_signals(
        self,
        signals: list[CorrelationSignal],
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> None:
        """
        Append species, breed and size evidence for animals.
        """
        self._append_text_signal(
            signals=signals,
            field="subject.species",
            query_value=query.subject.species,
            record_value=getattr(
                record.subject,
                "species",
                None,
            ),
            weight=self.ANIMAL_SPECIES_WEIGHT,
            description="species",
            match_threshold=(
                self.ANIMAL_TEXT_MATCH_THRESHOLD
            ),
            partial_threshold=(
                self.ANIMAL_TEXT_PARTIAL_THRESHOLD
            ),
            similarity_function=(
                self._descriptive_text_similarity
            ),
        )

        self._append_text_signal(
            signals=signals,
            field="subject.breed",
            query_value=query.subject.breed,
            record_value=getattr(
                record.subject,
                "breed",
                None,
            ),
            weight=self.ANIMAL_BREED_WEIGHT,
            description="breed",
            match_threshold=(
                self.ANIMAL_TEXT_MATCH_THRESHOLD
            ),
            partial_threshold=(
                self.ANIMAL_TEXT_PARTIAL_THRESHOLD
            ),
            similarity_function=(
                self._descriptive_text_similarity
            ),
        )

        self._append_exact_signal(
            signals=signals,
            field="subject.size",
            query_value=query.subject.size,
            record_value=getattr(
                record.subject,
                "size",
                None,
            ),
            weight=self.ANIMAL_SIZE_WEIGHT,
            description="animal size",
        )

    def _append_text_signal(
        self,
        signals: list[CorrelationSignal],
        field: str,
        query_value: str | None,
        record_value: str | None,
        weight: float,
        description: str,
        match_threshold: float,
        partial_threshold: float,
        similarity_function: Callable[
            [str, str],
            float,
        ],
    ) -> None:
        """
        Compare one text field and append an explainable signal.
        """
        if query_value is None:
            return

        if record_value is None:
            signals.append(
                CorrelationSignal(
                    field=field,
                    status=CorrelationSignalStatus.NOT_AVAILABLE,
                    contribution=0.0,
                    explanation=(
                        f"The candidate report does not contain "
                        f"{description} information."
                    ),
                    query_value=query_value,
                    record_value=None,
                )
            )

            return

        similarity = similarity_function(
            query_value,
            record_value,
        )

        status = self._similarity_status(
            similarity=similarity,
            match_threshold=match_threshold,
            partial_threshold=partial_threshold,
        )

        contribution = (
            self._contribution_for_similarity(
                similarity=similarity,
                status=status,
                weight=weight,
            )
        )

        signals.append(
            CorrelationSignal(
                field=field,
                status=status,
                contribution=contribution,
                explanation=self._text_signal_explanation(
                    description=description,
                    similarity=similarity,
                    status=status,
                ),
                query_value=query_value,
                record_value=record_value,
            )
        )

    def _append_exact_signal(
        self,
        signals: list[CorrelationSignal],
        field: str,
        query_value: object | None,
        record_value: object | None,
        weight: float,
        description: str,
    ) -> None:
        """
        Compare one canonical categorical value.
        """
        if query_value is None:
            return

        if record_value is None:
            signals.append(
                CorrelationSignal(
                    field=field,
                    status=CorrelationSignalStatus.NOT_AVAILABLE,
                    contribution=0.0,
                    explanation=(
                        f"The candidate report does not contain "
                        f"{description} information."
                    ),
                    query_value=query_value,
                    record_value=None,
                )
            )

            return

        query_normalized = self._normalize_text(
            str(query_value)
        )

        record_normalized = self._normalize_text(
            str(record_value)
        )

        if (
            query_normalized
            and query_normalized
            == record_normalized
        ):
            status = CorrelationSignalStatus.MATCH
            similarity = 1.0
            explanation = (
                f"The {description} values are equal."
            )

        else:
            status = CorrelationSignalStatus.CONFLICT
            similarity = 0.0
            explanation = (
                f"The {description} values are different."
            )

        contribution = (
            self._contribution_for_similarity(
                similarity=similarity,
                status=status,
                weight=weight,
            )
        )

        signals.append(
            CorrelationSignal(
                field=field,
                status=status,
                contribution=contribution,
                explanation=explanation,
                query_value=query_value,
                record_value=record_value,
            )
        )

    # ------------------------------------------------------------------
    # Compatibility and evidence strength
    # ------------------------------------------------------------------

    def _calculate_compatibility_score(
        self,
        signals: list[CorrelationSignal],
        subject_type: str,
    ) -> float:
        """
        Calculate compatibility among evidence that was actually compared.

        NOT_AVAILABLE evidence is excluded from the denominator.

        This prevents absent information from artificially lowering the
        similarity of fields that did match.
        """
        weights = self._weights_for_subject(
            subject_type
        )

        compared_weight = 0.0
        total_contribution = 0.0

        for signal in signals:
            if (
                signal.status
                == CorrelationSignalStatus.NOT_AVAILABLE
            ):
                continue

            weight = weights.get(
                signal.field
            )

            if weight is None:
                continue

            compared_weight += weight
            total_contribution += (
                signal.contribution
            )

        if compared_weight == 0.0:
            return 0.0

        score = (
            total_contribution
            / compared_weight
            * 100.0
        )

        return round(
            min(
                max(
                    score,
                    0.0,
                ),
                100.0,
            ),
            2,
        )

    def _calculate_evidence_strength(
        self,
        signals: list[CorrelationSignal],
        subject_type: str,
    ) -> float:
        """
        Calculate how much independent evidence supports the result.

        The denominator includes the complete evidence capacity for the
        selected subject type.

        Compatibility and evidence strength therefore remain separate.
        """
        weights = self._weights_for_subject(
            subject_type
        )

        total_possible_weight = sum(
            weights.values()
        )

        if total_possible_weight == 0.0:
            return 0.0

        supported_weight = 0.0

        for signal in signals:
            weight = weights.get(
                signal.field
            )

            if weight is None:
                continue

            if (
                signal.status
                == CorrelationSignalStatus.MATCH
            ):
                supported_weight += weight

            elif (
                signal.status
                == CorrelationSignalStatus.PARTIAL_MATCH
            ):
                similarity_fraction = (
                    signal.contribution / weight
                    if weight > 0
                    else 0.0
                )

                supported_weight += (
                    weight
                    * max(
                        0.25,
                        min(
                            similarity_fraction,
                            1.0,
                        ),
                    )
                )

        evidence_strength = (
            supported_weight
            / total_possible_weight
            * 100.0
        )

        return round(
            min(
                max(
                    evidence_strength,
                    0.0,
                ),
                100.0,
            ),
            2,
        )

    def _weights_for_subject(
        self,
        subject_type: str,
    ) -> dict[str, float]:
        """
        Return the evidence map used by score calculations.
        """
        if subject_type == "human":
            return {
                "observation.declared_location": (
                    self.HUMAN_SPATIAL_WEIGHT
                ),
                "observation.reported_location": (
                    self.HUMAN_SPATIAL_WEIGHT
                ),
                "observation.search_time": (
                    self.HUMAN_TEMPORAL_WEIGHT
                ),
                "subject.reported_label": (
                    self.HUMAN_NAME_WEIGHT
                ),
                "subject.estimated_age": (
                    self.HUMAN_AGE_WEIGHT
                ),
                "subject.recognition_features": (
                    self.HUMAN_FEATURES_WEIGHT
                ),
            }

        return {
            "observation.declared_location": (
                self.ANIMAL_SPATIAL_WEIGHT
            ),
            "observation.reported_location": (
                self.ANIMAL_SPATIAL_WEIGHT
            ),
            "observation.search_time": (
                self.ANIMAL_TEMPORAL_WEIGHT
            ),
            "subject.reported_label": (
                self.ANIMAL_NAME_WEIGHT
            ),
            "subject.species": (
                self.ANIMAL_SPECIES_WEIGHT
            ),
            "subject.breed": (
                self.ANIMAL_BREED_WEIGHT
            ),
            "subject.size": (
                self.ANIMAL_SIZE_WEIGHT
            ),
            "subject.recognition_features": (
                self.ANIMAL_FEATURES_WEIGHT
            ),
        }

    def _spatial_weight(
        self,
        subject_type: str,
    ) -> float:
        if subject_type == "human":
            return self.HUMAN_SPATIAL_WEIGHT

        return self.ANIMAL_SPATIAL_WEIGHT

    def _temporal_weight(
        self,
        subject_type: str,
    ) -> float:
        if subject_type == "human":
            return self.HUMAN_TEMPORAL_WEIGHT

        return self.ANIMAL_TEMPORAL_WEIGHT

    def _name_weight(
        self,
        subject_type: str,
    ) -> float:
        if subject_type == "human":
            return self.HUMAN_NAME_WEIGHT

        return self.ANIMAL_NAME_WEIGHT

    def _features_weight(
        self,
        subject_type: str,
    ) -> float:
        if subject_type == "human":
            return self.HUMAN_FEATURES_WEIGHT

        return self.ANIMAL_FEATURES_WEIGHT

    # ------------------------------------------------------------------
    # Explanation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _similarity_status(
        similarity: float,
        match_threshold: float,
        partial_threshold: float,
    ) -> CorrelationSignalStatus:
        if similarity >= match_threshold:
            return CorrelationSignalStatus.MATCH

        if similarity >= partial_threshold:
            return (
                CorrelationSignalStatus.PARTIAL_MATCH
            )

        return CorrelationSignalStatus.CONFLICT

    @staticmethod
    def _contribution_for_similarity(
        similarity: float,
        status: CorrelationSignalStatus,
        weight: float,
    ) -> float:
        if status == CorrelationSignalStatus.CONFLICT:
            return 0.0

        return round(
            weight * similarity,
            2,
        )

    @staticmethod
    def _text_signal_explanation(
        description: str,
        similarity: float,
        status: CorrelationSignalStatus,
    ) -> str:
        similarity_percentage = round(
            similarity * 100
        )

        if status == CorrelationSignalStatus.MATCH:
            return (
                f"The {description} evidence is strongly compatible "
                f"({similarity_percentage}% textual similarity)."
            )

        if (
            status
            == CorrelationSignalStatus.PARTIAL_MATCH
        ):
            return (
                f"The {description} evidence is partially compatible "
                f"({similarity_percentage}% textual similarity)."
            )

        return (
            f"The {description} evidence is not sufficiently compatible "
            f"({similarity_percentage}% textual similarity)."
        )

    @staticmethod
    def _legacy_location_explanation(
        similarity: float,
        status: CorrelationSignalStatus,
    ) -> str:
        percentage = round(
            similarity * 100
        )

        if status == CorrelationSignalStatus.MATCH:
            return (
                "The legacy free-text locations are strongly compatible "
                f"({percentage}% textual similarity)."
            )

        if (
            status
            == CorrelationSignalStatus.PARTIAL_MATCH
        ):
            return (
                "The legacy free-text locations are partially compatible "
                f"({percentage}% textual similarity)."
            )

        return (
            "The legacy free-text locations are not sufficiently compatible "
            f"({percentage}% textual similarity)."
        )

    # ------------------------------------------------------------------
    # Similarity helpers
    # ------------------------------------------------------------------

    @classmethod
    def _name_similarity(
        cls,
        first_value: str,
        second_value: str,
    ) -> float:
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
        first_set = set(
            first_tokens
        )

        second_set = set(
            second_tokens
        )

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
        if not first_tokens or not second_tokens:
            return 0.0

        first_name_similarity = (
            SequenceMatcher(
                None,
                first_tokens[0],
                second_tokens[0],
            ).ratio()
        )

        if first_name_similarity < 0.72:
            return 0.0

        first_surnames = (
            first_tokens[1:]
        )

        second_surnames = (
            second_tokens[1:]
        )

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
    def _location_level_similarity(
        first_value: str,
        second_value: str,
    ) -> float:
        return (
            CorrelationService
            ._descriptive_text_similarity(
                first_value,
                second_value,
            )
        )

    @staticmethod
    def _optional_location_level_similarity(
        first_value: str | None,
        second_value: str | None,
    ) -> float | None:
        if (
            first_value is None
            or second_value is None
        ):
            return None

        return (
            CorrelationService
            ._descriptive_text_similarity(
                first_value,
                second_value,
            )
        )

    @staticmethod
    def _normalize_text(
        value: str,
    ) -> str:
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

    # ------------------------------------------------------------------
    # Time helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _elapsed_days(
        searched_at: datetime | None,
        observed_at: datetime,
    ) -> float:
        if searched_at is None:
            return 0.0

        return max(
            0.0,
            (
                searched_at
                - observed_at
            ).total_seconds()
            / 86_400,
        )

    @staticmethod
    def _format_days(
        value: float,
    ) -> str:
        rounded_days = round(
            value
        )

        return (
            f"{rounded_days} day"
            f"{'' if rounded_days == 1 else 's'}"
        )

    @staticmethod
    def _confidence_sort_value(
        value: str,
    ) -> int:
        order = {
            "very_low": 0,
            "low": 1,
            "moderate": 2,
            "medium": 2,
            "high": 3,
            "very_high": 4,
        }

        return order.get(
            value,
            0,
        )


class SpatialComparison:
    """
    Internal explanation of structured geographic compatibility.

    This is an implementation object, not a canonical HCP model.
    """

    def __init__(
        self,
        status: CorrelationSignalStatus,
        similarity: float,
        explanation: str,
    ) -> None:
        self.status = status
        self.similarity = similarity
        self.explanation = explanation
