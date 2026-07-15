import unicodedata
from difflib import SequenceMatcher

from app.core.errors import CorrelationProcessingError
from app.models.correlation import (
    CorrelationResult,
    CorrelationSignal,
    CorrelationSignalStatus,
    confidence_from_score,
)
from app.models.humanitarian_case import HumanitarianCase
from app.models.humanitarian_record import HumanitarianRecord
from app.models.query import HumanitarianQuery


class CorrelationService:
    """
    Application service for explainable local correlation.

    Correlation estimates how compatible a Humanitarian Record is with the
    evidence supplied in a Humanitarian Query.

    It does not verify identity and must not present results as confirmed
    matches.
    """

    REPORTED_LABEL_WEIGHT = 30.0
    ESTIMATED_AGE_WEIGHT = 20.0
    RECOGNITION_FEATURES_WEIGHT = 25.0
    REPORTED_LOCATION_WEIGHT = 15.0
    EVENT_TYPE_WEIGHT = 10.0

    def correlate_records(
        self,
        query: HumanitarianQuery,
        records: list[HumanitarianRecord],
        limit: int | None = None,
        minimum_score: float = 0.0,
    ) -> list[CorrelationResult]:
        """
        Correlate one Query against a collection of candidate records.

        Args:
            query:
                Structured humanitarian evidence used for comparison.

            records:
                Candidate Humanitarian Records, normally supplied by
                SearchService.

            limit:
                Optional maximum number of correlation results.

            minimum_score:
                Minimum score required for a result to be returned.

        Returns:
            Correlation results ordered from strongest to weakest evidence.

        Raises:
            CorrelationProcessingError:
                If arguments are invalid or correlation cannot be completed.
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
                self.correlate_record(query, record)
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

        Only evidence supplied by the Query participates in the score.
        Missing record evidence is reported as unavailable and does not count
        as either a match or a conflict.
        """
        if query.subject.type != record.subject.type:
            raise CorrelationProcessingError(
                "Query and Humanitarian Record subject types must match"
            )

        signals: list[CorrelationSignal] = []

        self._append_text_signal(
            signals=signals,
            field="subject.reported_label",
            query_value=query.subject.reported_label,
            record_value=record.subject.reported_label,
            weight=self.REPORTED_LABEL_WEIGHT,
            description="reported label",
        )

        self._append_age_signal(
            signals=signals,
            query_age=query.subject.estimated_age,
            record_age=record.subject.estimated_age,
        )

        self._append_text_signal(
            signals=signals,
            field="subject.recognition_features",
            query_value=query.subject.recognition_features,
            record_value=record.subject.recognition_features,
            weight=self.RECOGNITION_FEATURES_WEIGHT,
            description="recognition features",
        )

        if query.observation is not None:
            self._append_text_signal(
                signals=signals,
                field="observation.reported_location",
                query_value=query.observation.reported_location,
                record_value=record.observation.reported_location,
                weight=self.REPORTED_LOCATION_WEIGHT,
                description="reported location",
            )

            self._append_exact_signal(
                signals=signals,
                field="observation.event_type",
                query_value=query.observation.event_type,
                record_value=record.observation.event_type,
                weight=self.EVENT_TYPE_WEIGHT,
                description="event type",
            )

        score = self._calculate_normalized_score(signals)

        return CorrelationResult(
            record_id=record.id,
            subject_type=record.subject.type,
            score=score,
            confidence=confidence_from_score(score),
            signals=signals,
        )

    def generate_case(
        self,
        query: HumanitarianQuery,
        results: list[CorrelationResult],
    ) -> HumanitarianCase:
        """
        Generate a local Humanitarian Case from correlation results.

        The case is a derived local interpretation. It does not alter or
        replace the original Humanitarian Records.
        """
        if not results:
            raise CorrelationProcessingError(
                "A Humanitarian Case requires at least one correlation result"
            )

        strongest_result = max(
            results,
            key=lambda result: result.score,
        )

        reasoning = (
            f"Generated from {len(results)} locally correlated "
            f"Humanitarian Record candidate"
            f"{'' if len(results) == 1 else 's'}. "
            f"The strongest candidate has a score of "
            f"{strongest_result.score:.2f} and confidence "
            f"'{strongest_result.confidence.value}'. "
            "The case represents probabilistic compatibility between "
            "humanitarian observations and requires human verification."
        )

        return HumanitarianCase(
            subject_type=query.subject.type,
            query=query,
            results=results,
            reasoning=reasoning,
        )

    def _append_text_signal(
        self,
        signals: list[CorrelationSignal],
        field: str,
        query_value: str | None,
        record_value: str | None,
        weight: float,
        description: str,
    ) -> None:
        """
        Compare one free-text evidence field and append an explainable signal.
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

        similarity = self._text_similarity(
            query_value,
            record_value,
        )

        status = self._similarity_status(similarity)
        contribution = self._contribution_for_similarity(
            similarity=similarity,
            status=status,
            weight=weight,
        )

        explanation = self._text_signal_explanation(
            description=description,
            similarity=similarity,
            status=status,
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

    def _append_age_signal(
        self,
        signals: list[CorrelationSignal],
        query_age: int | None,
        record_age: int | None,
    ) -> None:
        """
        Compare estimated human ages using progressively broader tolerances.
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

        age_difference = abs(query_age - record_age)

        if age_difference == 0:
            status = CorrelationSignalStatus.MATCH
            contribution = self.ESTIMATED_AGE_WEIGHT
            explanation = "The estimated ages are equal."

        elif age_difference <= 2:
            status = CorrelationSignalStatus.PARTIAL_MATCH
            contribution = self.ESTIMATED_AGE_WEIGHT * 0.85
            explanation = (
                "The estimated ages differ by no more than two years."
            )

        elif age_difference <= 5:
            status = CorrelationSignalStatus.PARTIAL_MATCH
            contribution = self.ESTIMATED_AGE_WEIGHT * 0.55
            explanation = (
                "The estimated ages are broadly compatible."
            )

        else:
            status = CorrelationSignalStatus.CONFLICT
            contribution = 0.0
            explanation = (
                "The estimated age difference is greater than five years."
            )

        signals.append(
            CorrelationSignal(
                field="subject.estimated_age",
                status=status,
                contribution=round(contribution, 2),
                explanation=explanation,
                query_value=query_age,
                record_value=record_age,
            )
        )

    def _append_exact_signal(
        self,
        signals: list[CorrelationSignal],
        field: str,
        query_value: str | None,
        record_value: str | None,
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
                        f"The candidate record does not contain "
                        f"{description} evidence."
                    ),
                    query_value=query_value,
                    record_value=None,
                )
            )
            return

        if query_value == record_value:
            status = CorrelationSignalStatus.MATCH
            contribution = weight
            explanation = f"The {description} values are equal."
        else:
            status = CorrelationSignalStatus.CONFLICT
            contribution = 0.0
            explanation = f"The {description} values are different."

        signals.append(
            CorrelationSignal(
                field=field,
                status=status,
                contribution=round(contribution, 2),
                explanation=explanation,
                query_value=query_value,
                record_value=record_value,
            )
        )

    @staticmethod
    def _calculate_normalized_score(
        signals: list[CorrelationSignal],
    ) -> float:
        """
        Calculate a score using only evidence supplied by the Query.

        Missing candidate evidence remains part of the available Query weight
        and therefore lowers the score without being treated as a conflict.
        """
        if not signals:
            return 0.0

        total_available_weight = 0.0
        total_contribution = 0.0

        weights = {
            "subject.reported_label": (
                CorrelationService.REPORTED_LABEL_WEIGHT
            ),
            "subject.estimated_age": (
                CorrelationService.ESTIMATED_AGE_WEIGHT
            ),
            "subject.recognition_features": (
                CorrelationService.RECOGNITION_FEATURES_WEIGHT
            ),
            "observation.reported_location": (
                CorrelationService.REPORTED_LOCATION_WEIGHT
            ),
            "observation.event_type": (
                CorrelationService.EVENT_TYPE_WEIGHT
            ),
        }

        for signal in signals:
            weight = weights.get(signal.field)

            if weight is None:
                continue

            total_available_weight += weight
            total_contribution += signal.contribution

        if total_available_weight == 0.0:
            return 0.0

        normalized_score = (
            total_contribution
            / total_available_weight
            * 100.0
        )

        return round(
            min(max(normalized_score, 0.0), 100.0),
            2,
        )

    @staticmethod
    def _similarity_status(
        similarity: float,
    ) -> CorrelationSignalStatus:
        """
        Convert textual similarity into an explainable signal status.
        """
        if similarity >= 0.85:
            return CorrelationSignalStatus.MATCH

        if similarity >= 0.45:
            return CorrelationSignalStatus.PARTIAL_MATCH

        return CorrelationSignalStatus.CONFLICT

    @staticmethod
    def _contribution_for_similarity(
        similarity: float,
        status: CorrelationSignalStatus,
        weight: float,
    ) -> float:
        """
        Calculate the positive contribution of one textual signal.
        """
        if status == CorrelationSignalStatus.CONFLICT:
            return 0.0

        return round(weight * similarity, 2)

    @staticmethod
    def _text_signal_explanation(
        description: str,
        similarity: float,
        status: CorrelationSignalStatus,
    ) -> str:
        """
        Build a human-readable explanation for a text comparison.
        """
        similarity_percentage = round(similarity * 100)

        if status == CorrelationSignalStatus.MATCH:
            return (
                f"The {description} evidence is strongly compatible "
                f"({similarity_percentage}% textual similarity)."
            )

        if status == CorrelationSignalStatus.PARTIAL_MATCH:
            return (
                f"The {description} evidence is partially compatible "
                f"({similarity_percentage}% textual similarity)."
            )

        return (
            f"The {description} evidence is not sufficiently compatible "
            f"({similarity_percentage}% textual similarity)."
        )

    @classmethod
    def _text_similarity(
        cls,
        first_value: str,
        second_value: str,
    ) -> float:
        """
        Calculate broad similarity between two human-entered text values.

        The comparison combines:

        - normalized character similarity;
        - token overlap;
        - direct containment.

        This approach remains deterministic and easy to explain.
        """
        first_normalized = cls._normalize_text(first_value)
        second_normalized = cls._normalize_text(second_value)

        if not first_normalized or not second_normalized:
            return 0.0

        if first_normalized == second_normalized:
            return 1.0

        containment_score = 0.0

        if (
            first_normalized in second_normalized
            or second_normalized in first_normalized
        ):
            shortest_length = min(
                len(first_normalized),
                len(second_normalized),
            )
            longest_length = max(
                len(first_normalized),
                len(second_normalized),
            )

            containment_score = shortest_length / longest_length

        sequence_score = SequenceMatcher(
            None,
            first_normalized,
            second_normalized,
        ).ratio()

        first_tokens = set(first_normalized.split())
        second_tokens = set(second_normalized.split())

        token_score = 0.0

        if first_tokens and second_tokens:
            token_score = (
                len(first_tokens.intersection(second_tokens))
                / len(first_tokens.union(second_tokens))
            )

        return round(
            max(
                containment_score,
                sequence_score,
                token_score,
            ),
            4,
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
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
            if not unicodedata.combining(character)
        )

        alphanumeric_text = "".join(
            character if character.isalnum() else " "
            for character in without_accents
        )

        return " ".join(alphanumeric_text.split())

