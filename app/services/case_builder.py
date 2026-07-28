from uuid import UUID

from app.core.errors import CorrelationProcessingError
from app.models.correlation import (
    CorrelationResult,
    CorrelationSignal,
    CorrelationSignalStatus,
)
from app.models.humanitarian_case import (
    CaseCorrelation,
    CaseVerification,
    CurrentSituation,
    EvidenceItem,
    HumanitarianCase,
    RelatedRecord,
    TimelineEntry,
)
from app.models.humanitarian_record import HumanitarianRecord
from app.models.query import HumanitarianQuery


class HumanitarianCaseBuilder:
    """
    Build a local Humanitarian Case from correlated Humanitarian Records.

    This service converts explainable correlation results into a local
    humanitarian interpretation.

    The builder separates three ideas:

    1. Descriptive compatibility
       How similar the available names, ages, recognition features and
       other descriptive fields are.

    2. Evidence strength
       How much independent evidence supports the strongest candidate.

    3. Observed situation
       What the most recent related Humanitarian Record reports.

    Event type does not determine identity compatibility. Different event
    types may represent different moments of the same humanitarian case.

    This service does not:

    - confirm identity;
    - merge canonical Humanitarian Records;
    - modify original evidence;
    - infer the cause of geographic or temporal differences;
    - persist the resulting Humanitarian Case;
    - synchronize the case between HCP Nodes.
    """

    def build(
        self,
        query: HumanitarianQuery,
        results: list[CorrelationResult],
        records: list[HumanitarianRecord],
    ) -> HumanitarianCase:
        """
        Build one Humanitarian Case from correlated records.

        The strongest correlation result provides the descriptive
        compatibility shown in the case.

        The most recent correlated record provides the current observed
        situation because a later hospital, shelter, safe or found
        observation may supersede an earlier missing report.

        Raises:
            CorrelationProcessingError:
                If no results are supplied or the inputs are inconsistent.
        """
        if not results:
            raise CorrelationProcessingError(
                "A Humanitarian Case requires at least one correlation result"
            )

        try:
            records_by_id = {
                record.id: record
                for record in records
            }

            ordered_results = sorted(
                results,
                key=lambda result: result.score,
                reverse=True,
            )

            self._validate_inputs(
                query=query,
                results=ordered_results,
                records_by_id=records_by_id,
            )

            strongest_result = ordered_results[0]

            strongest_record = records_by_id[
                strongest_result.record_id
            ]

            latest_record = self._latest_record(
                results=ordered_results,
                records_by_id=records_by_id,
            )

            related_records = self._build_related_records(
                results=ordered_results,
                records_by_id=records_by_id,
            )

            timeline = self._build_timeline(
                results=ordered_results,
                records_by_id=records_by_id,
            )

            supporting_evidence = self._build_evidence(
                results=ordered_results,
                status_group={
                    CorrelationSignalStatus.MATCH,
                    CorrelationSignalStatus.PARTIAL_MATCH,
                },
            )

            conflicting_evidence = self._build_evidence(
                results=ordered_results,
                status_group={
                    CorrelationSignalStatus.CONFLICT,
                },
            )

            reasoning = self._build_reasoning(
                results=ordered_results,
                strongest_record=strongest_record,
                latest_record=latest_record,
            )

            return HumanitarianCase(
                source_query_id=self._source_query_id(
                    query
                ),
                humanitarian_summary=self._build_summary(
                    result_count=len(
                        ordered_results
                    ),
                    strongest_result=strongest_result,
                    strongest_record=strongest_record,
                    latest_record=latest_record,
                ),
                current_situation=CurrentSituation(
                    likely_event_type=(
                        latest_record
                        .observation
                        .event_type
                    ),
                    reported_location=(
                        latest_record
                        .observation
                        .reported_location
                    ),
                    observed_at=(
                        latest_record
                        .observation
                        .observed_at
                    ),
                ),
                correlation=CaseCorrelation(
                    score=strongest_result.score,
                    evidence_level=(
                        strongest_result
                        .confidence
                        .value
                    ),
                    supporting_evidence=(
                        supporting_evidence
                    ),
                    conflicting_evidence=(
                        conflicting_evidence
                    ),
                    reasoning=reasoning,
                ),
                related_records=related_records,
                humanitarian_timeline=timeline,
                verification=CaseVerification(
                    status="unverified",
                    message=(
                        "This Humanitarian Case is a local probabilistic "
                        "interpretation. Descriptive compatibility does not "
                        "establish identity, and the result requires human "
                        "verification."
                    ),
                ),
            )

        except CorrelationProcessingError:
            raise

        except Exception as exc:
            raise CorrelationProcessingError(
                "Unable to build the local Humanitarian Case"
            ) from exc

    @staticmethod
    def _validate_inputs(
        query: HumanitarianQuery,
        results: list[CorrelationResult],
        records_by_id: dict[
            UUID,
            HumanitarianRecord,
        ],
    ) -> None:
        """
        Validate consistency among Query, results and source records.
        """
        result_ids = [
            result.record_id
            for result in results
        ]

        if len(result_ids) != len(set(result_ids)):
            raise CorrelationProcessingError(
                "Correlation results must not contain duplicate record "
                "identifiers"
            )

        for result in results:
            record = records_by_id.get(
                result.record_id
            )

            if record is None:
                raise CorrelationProcessingError(
                    "A correlation result references a Humanitarian Record "
                    "that was not supplied to the case builder"
                )

            if (
                result.subject_type
                != query.subject.type
            ):
                raise CorrelationProcessingError(
                    "All correlation results must match the Query subject type"
                )

            if (
                record.subject.type
                != query.subject.type
            ):
                raise CorrelationProcessingError(
                    "All related Humanitarian Records must match the Query "
                    "subject type"
                )

    @staticmethod
    def _latest_record(
        results: list[CorrelationResult],
        records_by_id: dict[
            UUID,
            HumanitarianRecord,
        ],
    ) -> HumanitarianRecord:
        """
        Return the most recent correlated Humanitarian Record.

        Event type is not used for identity compatibility. It is used here
        only to describe the latest known observation in the local timeline.
        """
        related_records = [
            records_by_id[result.record_id]
            for result in results
        ]

        if not related_records:
            raise CorrelationProcessingError(
                "Unable to determine the latest related observation"
            )

        return max(
            related_records,
            key=lambda record: (
                record.observation.observed_at
            ),
        )

    @staticmethod
    def _build_related_records(
        results: list[CorrelationResult],
        records_by_id: dict[
            UUID,
            HumanitarianRecord,
        ],
    ) -> list[RelatedRecord]:
        """
        Build record references in correlation-strength order.

        The ordering expresses descriptive compatibility, not chronology.
        Chronological order belongs to humanitarian_timeline.
        """
        return [
            RelatedRecord(
                record_id=result.record_id,
                event_type=(
                    records_by_id[
                        result.record_id
                    ].observation.event_type
                ),
                observed_at=(
                    records_by_id[
                        result.record_id
                    ].observation.observed_at
                ),
                source=(
                    records_by_id[
                        result.record_id
                    ].source_client
                ),
            )
            for result in results
        ]

    @staticmethod
    def _build_timeline(
        results: list[CorrelationResult],
        records_by_id: dict[
            UUID,
            HumanitarianRecord,
        ],
    ) -> list[TimelineEntry]:
        """
        Build an ascending chronological timeline.

        Different event types are intentionally preserved because they may
        describe the evolution of one humanitarian situation:

        - missing;
        - hospitalized;
        - sheltered;
        - safe;
        - found;
        - other compatible HCP observations.
        """
        timeline = [
            TimelineEntry(
                record_id=result.record_id,
                event_type=(
                    records_by_id[
                        result.record_id
                    ].observation.event_type
                ),
                observed_at=(
                    records_by_id[
                        result.record_id
                    ].observation.observed_at
                ),
                reported_location=(
                    records_by_id[
                        result.record_id
                    ].observation.reported_location
                ),
                description=(
                    "Humanitarian observation contributed by "
                    f"{records_by_id[result.record_id].source_client}."
                ),
            )
            for result in results
        ]

        timeline.sort(
            key=lambda entry: entry.observed_at
        )

        return timeline

    def _build_evidence(
        self,
        results: list[CorrelationResult],
        status_group: set[
            CorrelationSignalStatus
        ],
    ) -> list[EvidenceItem]:
        """
        Convert record-level signals into case-level evidence.

        Matching and partial-matching signals become supporting evidence.
        Conflicts remain visible for human review.

        NOT_AVAILABLE signals are not presented as contradictions.
        """
        evidence: list[EvidenceItem] = []

        for result in results:
            for signal in result.signals:
                if (
                    signal.status
                    not in status_group
                ):
                    continue

                evidence.append(
                    EvidenceItem(
                        type=self._evidence_type(
                            signal
                        ),
                        description=(
                            signal.explanation
                        ),
                        related_record_ids=[
                            result.record_id
                        ],
                    )
                )

        return evidence

    @staticmethod
    def _evidence_type(
        signal: CorrelationSignal,
    ) -> str:
        """
        Convert one correlation field into an evidence token.
        """
        field_token = (
            signal.field.replace(
                ".",
                "_",
            )
        )

        return (
            f"{field_token}_"
            f"{signal.status.value}"
        )

    @classmethod
    def _build_reasoning(
        cls,
        results: list[CorrelationResult],
        strongest_record: HumanitarianRecord,
        latest_record: HumanitarianRecord,
    ) -> str:
        """
        Build an explainable interpretation.

        The correlation score represents compatibility among evidence that
        was actually compared.

        The evidence level represents how much independent supporting
        evidence is available.
        """
        strongest_result = results[0]

        supporting_signal_count = sum(
            1
            for result in results
            for signal in result.signals
            if signal.status
            in {
                CorrelationSignalStatus.MATCH,
                CorrelationSignalStatus.PARTIAL_MATCH,
            }
        )

        conflicting_signal_count = sum(
            1
            for result in results
            for signal in result.signals
            if (
                signal.status
                == CorrelationSignalStatus.CONFLICT
            )
        )

        unavailable_signal_count = sum(
            1
            for result in results
            for signal in result.signals
            if (
                signal.status
                == CorrelationSignalStatus.NOT_AVAILABLE
            )
        )

        event_context = (
            cls._event_context_sentence(
                strongest_record=strongest_record,
                latest_record=latest_record,
            )
        )

        unavailable_context = (
            f" {unavailable_signal_count} requested evidence "
            f"field{'' if unavailable_signal_count == 1 else 's'} "
            "were unavailable in candidate records."
            if unavailable_signal_count > 0
            else ""
        )

        return (
            f"The case was generated from {len(results)} correlated "
            f"Humanitarian Record candidate"
            f"{'' if len(results) == 1 else 's'}. "
            f"The strongest candidate received a descriptive compatibility "
            f"score of {strongest_result.score:.2f} and an evidence strength "
            f"level of '{strongest_result.confidence.value}'. "
            f"The available results contain {supporting_signal_count} "
            f"supporting signal"
            f"{'' if supporting_signal_count == 1 else 's'} and "
            f"{conflicting_signal_count} conflicting signal"
            f"{'' if conflicting_signal_count == 1 else 's'}."
            f"{unavailable_context} "
            f"{event_context} "
            "This interpretation expresses compatibility between "
            "observations and does not establish identity."
        )

    @staticmethod
    def _event_context_sentence(
        strongest_record: HumanitarianRecord,
        latest_record: HumanitarianRecord,
    ) -> str:
        """
        Explain how event type is used in the case.

        Event type describes observations and timeline evolution. It does
        not support or contradict identity compatibility.
        """
        strongest_event = (
            strongest_record
            .observation
            .event_type
        )

        latest_event = (
            latest_record
            .observation
            .event_type
        )

        if strongest_event == latest_event:
            return (
                f"The latest related observation reports the event type "
                f"'{latest_event}'. Event type is presented as situational "
                "context and was not used as identity evidence."
            )

        return (
            f"The strongest descriptive candidate reports '{strongest_event}', "
            f"while the latest related observation reports '{latest_event}'. "
            "Different event types may represent different moments of the "
            "same humanitarian situation and were not treated as identity "
            "conflicts."
        )

    @classmethod
    def _build_summary(
        cls,
        result_count: int,
        strongest_result: CorrelationResult,
        strongest_record: HumanitarianRecord,
        latest_record: HumanitarianRecord,
    ) -> str:
        """
        Build a concise summary.

        Compatibility comes from the strongest descriptive candidate.
        Situation comes from the latest related observation.
        """
        latest_location = (
            latest_record
            .observation
            .reported_location
        )

        location_text = (
            f" in {latest_location}"
            if latest_location
            else ""
        )

        event_context = (
            cls._summary_event_context(
                strongest_record=strongest_record,
                latest_record=latest_record,
            )
        )

        return (
            f"{result_count} compatible Humanitarian Record candidate"
            f"{'' if result_count == 1 else 's'} were identified. "
            f"The strongest descriptive candidate received a compatibility "
            f"score of {strongest_result.score:.2f}. "
            f"{event_context} "
            f"The latest related observation reports "
            f"'{latest_record.observation.event_type}'"
            f"{location_text}. "
            "The result requires human verification."
        )

    @staticmethod
    def _summary_event_context(
        strongest_record: HumanitarianRecord,
        latest_record: HumanitarianRecord,
    ) -> str:
        """
        Add concise event context without presenting it as identity evidence.
        """
        strongest_event = (
            strongest_record
            .observation
            .event_type
        )

        latest_event = (
            latest_record
            .observation
            .event_type
        )

        if strongest_event == latest_event:
            return (
                "The event type is presented only as observed situational "
                "context."
            )

        return (
            f"The related records include different observed event types, "
            f"from '{strongest_event}' to '{latest_event}', which may "
            "represent different moments of the humanitarian timeline."
        )

    @staticmethod
    def _source_query_id(
        query: HumanitarianQuery,
    ) -> str | None:
        """
        Read the Query identifier without imposing one field name.

        This remains compatible while the Query model evolves between
        `id` and `query_id`.
        """
        query_id = getattr(
            query,
            "query_id",
            None,
        )

        if query_id is None:
            query_id = getattr(
                query,
                "id",
                None,
            )

        if query_id is None:
            return None

        return str(query_id)
