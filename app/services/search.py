import unicodedata
from collections.abc import Iterable
from difflib import SequenceMatcher

from app.core.errors import QueryProcessingError
from app.models.humanitarian_record import HumanitarianRecord
from app.models.query import HumanitarianQuery
from app.storage.base import RecordStorage


class SearchService:
    """
    Application service for broad local Humanitarian Record discovery.

    Search identifies records that are reasonable candidates for the later
    correlation stage.

    Candidate selection is intentionally permissive. Its purpose is to avoid
    discarding potentially useful humanitarian observations too early.

    Search does not:

    - verify identity;
    - calculate the final correlation score;
    - create Humanitarian Cases;
    - interpret why two observations may be geographically distant;
    - exclude a record merely because its event type is different.

    Event types such as missing, hospitalized, sheltered or safe describe
    different observations. They are not identity evidence and may belong to
    different moments of the same humanitarian case.
    """

    NAME_STRONG_THRESHOLD = 0.72
    NAME_WEAK_THRESHOLD = 0.50

    FEATURES_STRONG_THRESHOLD = 0.40
    FEATURES_WEAK_THRESHOLD = 0.25

    LOCATION_CONTEXT_THRESHOLD = 0.35

    ANIMAL_TEXT_STRONG_THRESHOLD = 0.65
    ANIMAL_TEXT_WEAK_THRESHOLD = 0.40

    STRONG_AGE_TOLERANCE = 3
    BROAD_AGE_TOLERANCE = 10

    def __init__(self, storage: RecordStorage) -> None:
        self.storage = storage

    def search_records(
        self,
        query: HumanitarianQuery,
        limit: int | None = None,
    ) -> list[HumanitarianRecord]:
        """
        Search locally stored Humanitarian Records using partial evidence.

        Candidate selection follows these principles:

        - subject type must match;
        - event type never excludes a candidate;
        - an exact or near-exact name is a strong candidate anchor;
        - human ages within plus or minus three years are strongly compatible;
        - broader age differences may remain useful during candidate discovery;
        - recognition features are treated as a high-value descriptive signal;
        - location is contextual and does not override strong descriptive
          compatibility;
        - missing evidence does not count as a contradiction;
        - final scoring and explanation belong to CorrelationService.

        Args:
            query:
                Structured HCP Query containing the available evidence.

            limit:
                Optional maximum number of candidates to return.

        Returns:
            Humanitarian Records broad enough to enter correlation.

        Raises:
            QueryProcessingError:
                If the supplied limit is invalid or search cannot be completed.
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

        Event type is intentionally ignored here. A person reported as
        missing may later appear in a hospital, shelter or safe observation.

        Location is also prevented from excluding a record when stronger
        descriptive evidence is compatible.
        """
        if record.subject.type != query.subject.type:
            return False

        descriptive_evidence = list(
            self._descriptive_compatibility_scores(
                query=query,
                record=record,
            )
        )

        strong_anchors = [
            evidence
            for evidence in descriptive_evidence
            if evidence.is_strong
        ]

        if strong_anchors:
            return True

        weak_compatible_signals = [
            evidence
            for evidence in descriptive_evidence
            if evidence.is_compatible
        ]

        if len(weak_compatible_signals) >= 2:
            return True

        supplied_descriptive_fields = [
            evidence
            for evidence in descriptive_evidence
            if evidence.was_compared
        ]

        if (
            len(supplied_descriptive_fields) == 1
            and weak_compatible_signals
        ):
            return True

        if supplied_descriptive_fields:
            return False

        return self._location_is_contextually_compatible(
            query=query,
            record=record,
        )

    def _descriptive_compatibility_scores(
        self,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> Iterable["CandidateEvidence"]:
        """
        Yield broad compatibility evidence for candidate discovery.

        Only values supplied by the Query participate.

        Missing values in the stored record are represented as unavailable,
        rather than as contradictions.
        """
        if query.subject.reported_label is not None:
            yield self._name_evidence(
                query_value=query.subject.reported_label,
                record_value=record.subject.reported_label,
            )

        if query.subject.estimated_age is not None:
            yield self._age_evidence(
                query_age=query.subject.estimated_age,
                record_age=record.subject.estimated_age,
            )

        if query.subject.recognition_features is not None:
            yield self._features_evidence(
                query_value=query.subject.recognition_features,
                record_value=record.subject.recognition_features,
            )

        if query.subject.type == "animal":
            query_species = getattr(
                query.subject,
                "species",
                None,
            )
            record_species = getattr(
                record.subject,
                "species",
                None,
            )

            if query_species is not None:
                yield self._animal_text_evidence(
                    field="species",
                    query_value=query_species,
                    record_value=record_species,
                )

            query_breed = getattr(
                query.subject,
                "breed",
                None,
            )
            record_breed = getattr(
                record.subject,
                "breed",
                None,
            )

            if query_breed is not None:
                yield self._animal_text_evidence(
                    field="breed",
                    query_value=query_breed,
                    record_value=record_breed,
                )

            query_size = getattr(
                query.subject,
                "size",
                None,
            )
            record_size = getattr(
                record.subject,
                "size",
                None,
            )

            if query_size is not None:
                yield self._exact_evidence(
                    field="size",
                    query_value=query_size,
                    record_value=record_size,
                )

    def _name_evidence(
        self,
        query_value: str,
        record_value: str | None,
    ) -> "CandidateEvidence":
        """
        Compare names and reported labels permissively.

        This comparison supports:

        - full-name equality;
        - first name plus one surname;
        - first name plus two surnames;
        - different surname order;
        - accents and punctuation differences;
        - small typing variations such as Maria / Marias;
        - containment between shorter and longer forms.
        """
        if record_value is None:
            return CandidateEvidence.unavailable(
                field="reported_label",
            )

        similarity = self._name_similarity(
            query_value,
            record_value,
        )

        return CandidateEvidence(
            field="reported_label",
            score=similarity,
            was_compared=True,
            is_compatible=(
                similarity >= self.NAME_WEAK_THRESHOLD
            ),
            is_strong=(
                similarity >= self.NAME_STRONG_THRESHOLD
            ),
        )

    def _age_evidence(
        self,
        query_age: int,
        record_age: int | None,
    ) -> "CandidateEvidence":
        """
        Compare estimated human ages.

        Plus or minus three years is considered a strong candidate anchor.

        A broader tolerance remains available during candidate discovery so
        that estimation errors do not discard a useful record prematurely.
        Final interpretation belongs to CorrelationService.
        """
        if record_age is None:
            return CandidateEvidence.unavailable(
                field="estimated_age",
            )

        difference = abs(query_age - record_age)

        if difference <= self.STRONG_AGE_TOLERANCE:
            return CandidateEvidence(
                field="estimated_age",
                score=1.0 - (
                    difference
                    / (
                        self.STRONG_AGE_TOLERANCE
                        + 1
                    )
                    * 0.15
                ),
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
                was_compared=True,
                is_compatible=True,
                is_strong=False,
            )

        return CandidateEvidence(
            field="estimated_age",
            score=0.0,
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
        Compare recognition features as high-value humanitarian evidence.

        Descriptions are intentionally compared more permissively than names.

        A short phrase such as "pastor alemán" may be useful against a longer
        report such as "perra color caramelo, pastor alemán".
        """
        if record_value is None:
            return CandidateEvidence.unavailable(
                field="recognition_features",
            )

        similarity = self._descriptive_text_similarity(
            query_value,
            record_value,
        )

        return CandidateEvidence(
            field="recognition_features",
            score=similarity,
            was_compared=True,
            is_compatible=(
                similarity >= self.FEATURES_WEAK_THRESHOLD
            ),
            is_strong=(
                similarity >= self.FEATURES_STRONG_THRESHOLD
            ),
        )

    def _animal_text_evidence(
        self,
        field: str,
        query_value: str,
        record_value: str | None,
    ) -> "CandidateEvidence":
        """
        Compare animal species or breed using normalized text similarity.
        """
        if record_value is None:
            return CandidateEvidence.unavailable(
                field=field,
            )

        similarity = self._descriptive_text_similarity(
            query_value,
            record_value,
        )

        return CandidateEvidence(
            field=field,
            score=similarity,
            was_compared=True,
            is_compatible=(
                similarity >= self.ANIMAL_TEXT_WEAK_THRESHOLD
            ),
            is_strong=(
                similarity >= self.ANIMAL_TEXT_STRONG_THRESHOLD
            ),
        )

    def _exact_evidence(
        self,
        field: str,
        query_value: object,
        record_value: object | None,
    ) -> "CandidateEvidence":
        """
        Compare one normalized categorical value.
        """
        if record_value is None:
            return CandidateEvidence.unavailable(
                field=field,
            )

        query_normalized = self._normalize_text(
            str(query_value)
        )
        record_normalized = self._normalize_text(
            str(record_value)
        )

        is_equal = (
            query_normalized
            and query_normalized == record_normalized
        )

        return CandidateEvidence(
            field=field,
            score=1.0 if is_equal else 0.0,
            was_compared=True,
            is_compatible=bool(is_equal),
            is_strong=bool(is_equal),
        )

    def _location_is_contextually_compatible(
        self,
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> bool:
        """
        Use location only when no descriptive criteria were available.

        Location is contextual evidence. A different free-text location must
        not override a compatible name, age or physical description.

        Future HCP versions may replace this with structured country, region,
        locality and space-time plausibility rules.
        """
        if (
            query.observation is None
            or query.observation.reported_location is None
        ):
            return False

        record_location = (
            record.observation.reported_location
        )

        if record_location is None:
            return False

        similarity = self._descriptive_text_similarity(
            query.observation.reported_location,
            record_location,
        )

        return (
            similarity
            >= self.LOCATION_CONTEXT_THRESHOLD
        )

    @classmethod
    def _name_similarity(
        cls,
        first_value: str,
        second_value: str,
    ) -> float:
        """
        Calculate similarity between human-entered names.

        The final value combines:

        - normalized character similarity;
        - exact token overlap;
        - containment;
        - best token-to-token similarity;
        - surname/order tolerance.
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

        fuzzy_token_score = cls._fuzzy_token_overlap_score(
            first_tokens,
            second_tokens,
        )

        ordered_core_score = cls._ordered_name_core_score(
            first_tokens,
            second_tokens,
        )

        return round(
            max(
                sequence_score,
                containment_score,
                exact_token_score,
                fuzzy_token_score,
                ordered_core_score,
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
        Calculate broad similarity between free-text descriptions.

        This is more tolerant than strict full-string matching because
        different observers may describe the same visible features using
        different sentence lengths or word orders.
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

        token_overlap = cls._token_overlap_score(
            first_tokens,
            second_tokens,
        )

        fuzzy_token_overlap = (
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
                token_overlap,
                fuzzy_token_overlap,
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
        Score direct containment without treating every substring as exact.
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
        Calculate exact token overlap using the shorter side as reference.

        This allows "Sandro Cantor" to remain strongly compatible with a
        longer label that contains those same name tokens.
        """
        first_set = set(first_tokens)
        second_set = set(second_tokens)

        if not first_set or not second_set:
            return 0.0

        shared_tokens = (
            first_set.intersection(second_set)
        )

        shortest_token_count = min(
            len(first_set),
            len(second_set),
        )

        if shortest_token_count == 0:
            return 0.0

        return (
            len(shared_tokens)
            / shortest_token_count
        )

    @staticmethod
    def _fuzzy_token_overlap_score(
        first_tokens: list[str],
        second_tokens: list[str],
    ) -> float:
        """
        Match tokens while tolerating minor typing differences.

        Example:
            Maria / Marias
        """
        if not first_tokens or not second_tokens:
            return 0.0

        shorter_tokens = (
            first_tokens
            if len(first_tokens)
            <= len(second_tokens)
            else second_tokens
        )

        longer_tokens = (
            second_tokens
            if shorter_tokens is first_tokens
            else first_tokens
        )

        matched_scores: list[float] = []

        for token in shorter_tokens:
            best_score = max(
                SequenceMatcher(
                    None,
                    token,
                    candidate_token,
                ).ratio()
                for candidate_token in longer_tokens
            )

            if best_score >= 0.72:
                matched_scores.append(best_score)

        if not shorter_tokens:
            return 0.0

        coverage = (
            len(matched_scores)
            / len(shorter_tokens)
        )

        if not matched_scores:
            return 0.0

        average_similarity = (
            sum(matched_scores)
            / len(matched_scores)
        )

        return coverage * average_similarity

    @staticmethod
    def _ordered_name_core_score(
        first_tokens: list[str],
        second_tokens: list[str],
    ) -> float:
        """
        Preserve useful name compatibility when one observer supplied fewer
        surnames or used a different surname order.
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

        first_remaining = set(first_tokens[1:])
        second_remaining = set(second_tokens[1:])

        if not first_remaining or not second_remaining:
            return first_name_similarity * 0.75

        surname_overlap = (
            len(
                first_remaining.intersection(
                    second_remaining
                )
            )
            / min(
                len(first_remaining),
                len(second_remaining),
            )
        )

        return (
            first_name_similarity * 0.55
            + surname_overlap * 0.45
        )

    @classmethod
    def _shorter_text_coverage(
        cls,
        first_tokens: list[str],
        second_tokens: list[str],
    ) -> float:
        """
        Measure how much of the shorter description is represented in the
        longer one, including small token variants.
        """
        if not first_tokens or not second_tokens:
            return 0.0

        shorter_tokens = (
            first_tokens
            if len(first_tokens)
            <= len(second_tokens)
            else second_tokens
        )

        longer_tokens = (
            second_tokens
            if shorter_tokens is first_tokens
            else first_tokens
        )

        covered = 0.0

        for token in shorter_tokens:
            best_similarity = max(
                SequenceMatcher(
                    None,
                    token,
                    candidate_token,
                ).ratio()
                for candidate_token in longer_tokens
            )

            if best_similarity >= 0.72:
                covered += best_similarity

        return (
            covered
            / len(shorter_tokens)
        )

    @staticmethod
    def _normalize_text(
        value: str,
    ) -> str:
        """
        Normalize human-entered text for deterministic comparison.

        Normalization:

        - removes surrounding whitespace;
        - compares case-insensitively;
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


class CandidateEvidence:
    """
    Internal candidate-selection evidence.

    This class is deliberately local to SearchService. It is not a canonical
    HCP model and must not be serialized or synchronized between nodes.
    """

    def __init__(
        self,
        field: str,
        score: float,
        was_compared: bool,
        is_compatible: bool,
        is_strong: bool,
    ) -> None:
        self.field = field
        self.score = score
        self.was_compared = was_compared
        self.is_compatible = is_compatible
        self.is_strong = is_strong

    @classmethod
    def unavailable(
        cls,
        field: str,
    ) -> "CandidateEvidence":
        return cls(
            field=field,
            score=0.0,
            was_compared=False,
            is_compatible=False,
            is_strong=False,
        )
