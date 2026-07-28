import unicodedata
from difflib import SequenceMatcher

from app.core.errors import CorrelationProcessingError
from app.models.correlation import (
    CorrelationResult,
    CorrelationSignal,
    CorrelationSignalStatus,
    confidence_from_score,
)
from app.models.humanitarian_record import HumanitarianRecord
from app.models.query import HumanitarianQuery


class CorrelationService:
    """
    Application service for explainable local correlation.

    Correlation answers two different questions:

    1. Compatibility:
       How similar is the evidence that was actually compared?

    2. Evidence strength:
       How much independent evidence supports that compatibility?

    These concepts must remain separate.

    For example:

    - exact name + exact age:
      high compatibility, but moderate evidence strength;

    - exact name + age + recognition features + animal attributes:
      high compatibility and high evidence strength.

    Correlation does not:

    - verify identity;
    - interpret the cause of geographic distance;
    - exclude records merely because event types differ;
    - create Humanitarian Cases;
    - modify Humanitarian Records;
    - persist results.

    Event type describes what was observed. It is not identity evidence.

    A missing, hospitalized, sheltered or safe observation may represent
    a different moment of the same humanitarian case.
    """

    # ------------------------------------------------------------------
    # Human evidence weights
    # ------------------------------------------------------------------

    HUMAN_REPORTED_LABEL_WEIGHT = 30.0
    HUMAN_ESTIMATED_AGE_WEIGHT = 20.0
    HUMAN_RECOGNITION_FEATURES_WEIGHT = 40.0
    HUMAN_REPORTED_LOCATION_WEIGHT = 10.0

    # ------------------------------------------------------------------
    # Animal evidence weights
    # ------------------------------------------------------------------

    ANIMAL_REPORTED_LABEL_WEIGHT = 20.0
    ANIMAL_SPECIES_WEIGHT = 15.0
    ANIMAL_BREED_WEIGHT = 10.0
    ANIMAL_SIZE_WEIGHT = 10.0
    ANIMAL_RECOGNITION_FEATURES_WEIGHT = 35.0
    ANIMAL_REPORTED_LOCATION_WEIGHT = 10.0

    # ------------------------------------------------------------------
    # Similarity thresholds
    # ------------------------------------------------------------------

    NAME_MATCH_THRESHOLD = 0.85
    NAME_PARTIAL_THRESHOLD = 0.50

    FEATURES_MATCH_THRESHOLD = 0.75
    FEATURES_PARTIAL_THRESHOLD = 0.25

    LOCATION_MATCH_THRESHOLD = 0.80
    LOCATION_PARTIAL_THRESHOLD = 0.30

    ANIMAL_TEXT_MATCH_THRESHOLD = 0.85
    ANIMAL_TEXT_PARTIAL_THRESHOLD = 0.45

    # ------------------------------------------------------------------
    # Age rules
    # ------------------------------------------------------------------

    STRONG_AGE_TOLERANCE = 3
    BROAD_AGE_TOLERANCE = 7

    def correlate_records(
        self,
        query: HumanitarianQuery,
        records: list[HumanitarianRecord],
        limit: int | None = None,
        minimum_score: float = 0.0,
    ) -> list[CorrelationResult]:
        """
        Correlate one Query against candidate Humanitarian Records.

        Only records with the same subject type participate.

        The returned score represents compatibility among evidence that was
        actually available and compared.

        Confidence represents evidence strength and therefore also considers
        how much independent evidence was supplied.
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
            results = [
                self.correlate_record(
                    query=query,
                    record=record,
                )
                for record in records
                if record.subject.type == query.subject.type
            ]

            filtered_results = [
                result
                for result in results
                if result.score >= minimum_score
            ]

            filtered_results.sort(
                key=lambda result: result.score,
                reverse=True,
            )

            if limit is not None:
                return filtered_results[:limit]

            return filtered_results

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
        Correlate one Query with one Humanitarian Record.

        Important rules:

        - only evidence supplied by the Query participates;
        - unavailable record evidence does not count as a conflict;
        - event type does not participate in identity compatibility;
        - location has a low contextual weight;
        - descriptive evidence carries most of the correlation value.
        """
        if query.subject.type != record.subject.type:
            raise CorrelationProcessingError(
                "Query and Humanitarian Record subject types must match"
            )

        signals: list[CorrelationSignal] = []

        if query.subject.type == "human":
            self._append_human_signals(
                signals=signals,
                query=query,
                record=record,
            )

        else:
            self._append_animal_signals(
                signals=signals,
                query=query,
                record=record,
            )

        self._append_location_signal(
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

        evidence_strength_score = (
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
                evidence_strength_score
            ),
            signals=signals,
        )

    # ------------------------------------------------------------------
    # Signal construction
    # ------------------------------------------------------------------

    def _append_human_signals(
        self,
        signals: list[CorrelationSignal],
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> None:
        """
        Append descriptive evidence for a human subject.
        """
        self._append_name_signal(
            signals=signals,
            query_value=query.subject.reported_label,
            record_value=record.subject.reported_label,
            weight=self.HUMAN_REPORTED_LABEL_WEIGHT,
        )

        self._append_age_signal(
            signals=signals,
            query_age=query.subject.estimated_age,
            record_age=record.subject.estimated_age,
            weight=self.HUMAN_ESTIMATED_AGE_WEIGHT,
        )

        self._append_features_signal(
            signals=signals,
            query_value=query.subject.recognition_features,
            record_value=record.subject.recognition_features,
            weight=self.HUMAN_RECOGNITION_FEATURES_WEIGHT,
        )

    def _append_animal_signals(
        self,
        signals: list[CorrelationSignal],
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> None:
        """
        Append descriptive evidence for an animal subject.
        """
        self._append_name_signal(
            signals=signals,
            query_value=query.subject.reported_label,
            record_value=record.subject.reported_label,
            weight=self.ANIMAL_REPORTED_LABEL_WEIGHT,
        )

        self._append_animal_text_signal(
            signals=signals,
            field="subject.species",
            query_value=getattr(
                query.subject,
                "species",
                None,
            ),
            record_value=getattr(
                record.subject,
                "species",
                None,
            ),
            weight=self.ANIMAL_SPECIES_WEIGHT,
            description="species",
        )

        self._append_animal_text_signal(
            signals=signals,
            field="subject.breed",
            query_value=getattr(
                query.subject,
                "breed",
                None,
            ),
            record_value=getattr(
                record.subject,
                "breed",
                None,
            ),
            weight=self.ANIMAL_BREED_WEIGHT,
            description="breed",
        )

        self._append_exact_signal(
            signals=signals,
            field="subject.size",
            query_value=getattr(
                query.subject,
                "size",
                None,
            ),
            record_value=getattr(
                record.subject,
                "size",
                None,
            ),
            weight=self.ANIMAL_SIZE_WEIGHT,
            description="animal size",
        )

        self._append_features_signal(
            signals=signals,
            query_value=query.subject.recognition_features,
            record_value=record.subject.recognition_features,
            weight=self.ANIMAL_RECOGNITION_FEATURES_WEIGHT,
        )

    def _append_location_signal(
        self,
        signals: list[CorrelationSignal],
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> None:
        """
        Append free-text location as contextual evidence.

        Location is deliberately weak in schema version 0.5 because it is
        currently an unstructured user-entered string.

        A different location can lower compatibility slightly, but it cannot
        override strong name, age or recognition-feature evidence.

        Future versions may replace this with structured country, region,
        locality and space-time plausibility rules.
        """
        if query.observation is None:
            return

        query_location = (
            query.observation.reported_location
        )

        if query_location is None:
            return

        location_weight = (
            self.HUMAN_REPORTED_LOCATION_WEIGHT
            if query.subject.type == "human"
            else self.ANIMAL_REPORTED_LOCATION_WEIGHT
        )

        self._append_text_signal(
            signals=signals,
            field="observation.reported_location",
            query_value=query_location,
            record_value=record.observation.reported_location,
            weight=location_weight,
            description="reported location",
            match_threshold=self.LOCATION_MATCH_THRESHOLD,
            partial_threshold=self.LOCATION_PARTIAL_THRESHOLD,
            similarity_function=self._descriptive_text_similarity,
        )

    def _append_name_signal(
        self,
        signals: list[CorrelationSignal],
        query_value: str | None,
        record_value: str | None,
        weight: float,
    ) -> None:
        """
        Compare a reported name or label.

        Supports:

        - exact names;
        - partial names;
        - one or two surnames;
        - surname order differences;
        - accents and punctuation differences;
        - small typing variants such as Maria / Marias.
        """
        self._append_text_signal(
            signals=signals,
            field="subject.reported_label",
            query_value=query_value,
            record_value=record_value,
            weight=weight,
            description="reported label",
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
        Compare recognition features as high-value descriptive evidence.

        Recognition features receive the highest individual weight because
        they offer the greatest opportunity to connect observations created
        independently by different people.
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
            similarity_function=self._descriptive_text_similarity,
        )

    def _append_animal_text_signal(
        self,
        signals: list[CorrelationSignal],
        field: str,
        query_value: str | None,
        record_value: str | None,
        weight: float,
        description: str,
    ) -> None:
        """
        Compare animal species or breed.
        """
        self._append_text_signal(
            signals=signals,
            field=field,
            query_value=query_value,
            record_value=record_value,
            weight=weight,
            description=description,
            match_threshold=self.ANIMAL_TEXT_MATCH_THRESHOLD,
            partial_threshold=self.ANIMAL_TEXT_PARTIAL_THRESHOLD,
            similarity_function=self._descriptive_text_similarity,
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
        similarity_function,
    ) -> None:
        """
        Compare one free-text field and append an explainable signal.
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
                        f"The candidate record does not contain "
                        f"{description} evidence."
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

        contribution = self._contribution_for_similarity(
            similarity=similarity,
            status=status,
            weight=weight,
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

    def _append_age_signal(
        self,
        signals: list[CorrelationSignal],
        query_age: int | None,
        record_age: int | None,
        weight: float,
    ) -> None:
        """
        Compare estimated human ages.

        Rules:

        - exact age:
          full match;

        - difference of one to three years:
          strong compatibility;

        - difference of four to seven years:
          partial compatibility;

        - difference greater than seven years:
          conflict.

        Age remains approximate evidence and must never establish identity.
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
                        "The candidate record does not contain estimated "
                        "age evidence."
                    ),
                    query_value=query_age,
                    record_value=None,
                )
            )
            return

        difference = abs(query_age - record_age)

        if difference == 0:
            status = CorrelationSignalStatus.MATCH
            similarity = 1.0
            explanation = "The estimated ages are equal."

        elif difference <= self.STRONG_AGE_TOLERANCE:
            status = CorrelationSignalStatus.MATCH

            similarity = (
                1.0
                - (
                    difference
                    / (
                        self.STRONG_AGE_TOLERANCE
                        + 1
                    )
                    * 0.12
                )
            )

            explanation = (
                "The estimated ages are strongly compatible "
                f"within a difference of {difference} "
                f"year{'' if difference == 1 else 's'}."
            )

        elif difference <= self.BROAD_AGE_TOLERANCE:
            status = CorrelationSignalStatus.PARTIAL_MATCH

            similarity = max(
                0.45,
                1.0
                - (
                    difference
                    / self.BROAD_AGE_TOLERANCE
                ),
            )

            explanation = (
                "The estimated ages are partially compatible "
                f"with a difference of {difference} years."
            )

        else:
            status = CorrelationSignalStatus.CONFLICT
            similarity = 0.0
            explanation = (
                "The estimated ages are conflicting "
                f"with a difference of {difference} years."
            )

        contribution = (
            weight * similarity
            if status != CorrelationSignalStatus.CONFLICT
            else 0.0
        )

        signals.append(
            CorrelationSignal(
                field="subject.estimated_age",
                status=status,
                contribution=round(
                    contribution,
                    2,
                ),
                explanation=explanation,
                query_value=query_age,
                record_value=record_age,
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

        Used for fields such as animal size, not event type.
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
                        f"The candidate record does not contain "
                        f"{description} evidence."
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
            and query_normalized == record_normalized
        ):
            status = CorrelationSignalStatus.MATCH
            contribution = weight
            explanation = (
                f"The {description} values are equal."
            )

        else:
            status = CorrelationSignalStatus.CONFLICT
            contribution = 0.0
            explanation = (
                f"The {description} values are different."
            )

        signals.append(
            CorrelationSignal(
                field=field,
                status=status,
                contribution=round(
                    contribution,
                    2,
                ),
                explanation=explanation,
                query_value=query_value,
                record_value=record_value,
            )
        )

    # ------------------------------------------------------------------
    # Score calculation
    # ------------------------------------------------------------------

    def _calculate_compatibility_score(
        self,
        signals: list[CorrelationSignal],
        subject_type: str,
    ) -> float:
        """
        Calculate similarity among evidence that was actually compared.

        Missing candidate evidence is excluded from the denominator.

        Therefore:

        - exact name + exact age can produce 100% compatibility;
        - the result may still have only moderate evidence strength because
          few independent fields were supplied.
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

            weight = weights.get(signal.field)

            if weight is None:
                continue

            compared_weight += weight
            total_contribution += signal.contribution

        if compared_weight == 0.0:
            return 0.0

        score = (
            total_contribution
            / compared_weight
            * 100.0
        )

        return round(
            min(max(score, 0.0), 100.0),
            2,
        )

    def _calculate_evidence_strength(
        self,
        signals: list[CorrelationSignal],
        subject_type: str,
    ) -> float:
        """
        Estimate how much independent evidence supports the result.

        This value is used only to derive the confidence/evidence level.

        Unlike compatibility, its denominator includes the complete
        descriptive evidence capacity for the subject type.

        Consequently:

        - exact name alone:
          high compatibility, low evidence strength;

        - exact name and age:
          high compatibility, moderate evidence strength;

        - name, age and recognition features:
          high compatibility and high evidence strength.
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
            weight = weights.get(signal.field)

            if weight is None:
                continue

            if signal.status == CorrelationSignalStatus.MATCH:
                supported_weight += weight

            elif (
                signal.status
                == CorrelationSignalStatus.PARTIAL_MATCH
            ):
                if weight <= 0.0:
                    continue

                similarity_fraction = (
                    signal.contribution / weight
                )

                supported_weight += (
                    weight
                    * max(
                        0.35,
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
        Return evidence weights for one subject type.
        """
        if subject_type == "human":
            return {
                "subject.reported_label": (
                    self.HUMAN_REPORTED_LABEL_WEIGHT
                ),
                "subject.estimated_age": (
                    self.HUMAN_ESTIMATED_AGE_WEIGHT
                ),
                "subject.recognition_features": (
                    self.HUMAN_RECOGNITION_FEATURES_WEIGHT
                ),
                "observation.reported_location": (
                    self.HUMAN_REPORTED_LOCATION_WEIGHT
                ),
            }

        return {
            "subject.reported_label": (
                self.ANIMAL_REPORTED_LABEL_WEIGHT
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
                self.ANIMAL_RECOGNITION_FEATURES_WEIGHT
            ),
            "observation.reported_location": (
                self.ANIMAL_REPORTED_LOCATION_WEIGHT
            ),
        }

    # ------------------------------------------------------------------
    # Similarity helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _similarity_status(
        similarity: float,
        match_threshold: float,
        partial_threshold: float,
    ) -> CorrelationSignalStatus:
        """
        Convert similarity into an explainable signal status.
        """
        if similarity >= match_threshold:
            return CorrelationSignalStatus.MATCH

        if similarity >= partial_threshold:
            return CorrelationSignalStatus.PARTIAL_MATCH

        return CorrelationSignalStatus.CONFLICT

    @staticmethod
    def _contribution_for_similarity(
        similarity: float,
        status: CorrelationSignalStatus,
        weight: float,
    ) -> float:
        """
        Calculate the positive contribution of one signal.
        """
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
        """
        Build a human-readable explanation for a text comparison.
        """
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

    @classmethod
    def _name_similarity(
        cls,
        first_value: str,
        second_value: str,
    ) -> float:
        """
        Calculate similarity between human-entered names.

        The comparison supports partial names, surname order differences
        and small typing variations.
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

        sequence_score = SequenceMatcher(
            None,
            first_normalized,
            second_normalized,
        ).ratio()

        containment_score = cls._containment_score(
            first_normalized,
            second_normalized,
        )

        exact_token_score = cls._token_overlap_score(
            first_tokens,
            second_tokens,
        )

        fuzzy_token_score = (
            cls._fuzzy_token_overlap_score(
                first_tokens,
                second_tokens,
            )
        )

        name_core_score = cls._name_core_score(
            first_tokens,
            second_tokens,
        )

        return round(
            max(
                sequence_score,
                containment_score,
                exact_token_score,
                fuzzy_token_score,
                name_core_score,
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
        Calculate similarity between human-entered descriptions.

        Short descriptions can remain strongly compatible with longer
        descriptions when their meaningful words are contained or closely
        represented.
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

        sequence_score = SequenceMatcher(
            None,
            first_normalized,
            second_normalized,
        ).ratio()

        containment_score = cls._containment_score(
            first_normalized,
            second_normalized,
        )

        exact_token_score = cls._token_overlap_score(
            first_tokens,
            second_tokens,
        )

        fuzzy_token_score = (
            cls._fuzzy_token_overlap_score(
                first_tokens,
                second_tokens,
            )
        )

        coverage_score = cls._shorter_text_coverage(
            first_tokens,
            second_tokens,
        )

        return round(
            max(
                sequence_score,
                containment_score,
                exact_token_score,
                fuzzy_token_score,
                coverage_score,
            ),
            4,
        )

    @staticmethod
    def _containment_score(
        first_value: str,
        second_value: str,
    ) -> float:
        """
        Score direct containment.
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

        raw_ratio = (
            shortest_length
            / longest_length
        )

        return max(
            raw_ratio,
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

        shared = (
            first_set.intersection(
                second_set
            )
        )

        denominator = min(
            len(first_set),
            len(second_set),
        )

        if denominator == 0:
            return 0.0

        return (
            len(shared)
            / denominator
        )

    @staticmethod
    def _fuzzy_token_overlap_score(
        first_tokens: list[str],
        second_tokens: list[str],
    ) -> float:
        """
        Measure token overlap while tolerating minor typing variations.
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

    @staticmethod
    def _name_core_score(
        first_tokens: list[str],
        second_tokens: list[str],
    ) -> float:
        """
        Compare first name and available surnames without requiring equal
        name length or surname order.
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
            CorrelationService
            ._fuzzy_token_overlap_score(
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
        Measure how much of the shorter description appears in the longer
        description.
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
