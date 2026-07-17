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

    This service converts correlation results into an explainable
    humanitarian interpretation.

    It does not:

    - create new canonical Humanitarian Records;
    - confirm the identity of a human or animal;
    - modify the original evidence;
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
        Build one Humanitarian Case from correlation results and records.

        Args:
            query:
                Humanitarian Query that originated the search.

            results:
                Explainable correlation results ordered or unordered.

            records:
                Candidate Humanitarian Records used during correlation.

        Returns:
            A local Humanitarian Case containing the strongest current
            interpretation, related evidence and chronological timeline.

        Raises:
            CorrelationProcessingError:
                If no correlation results are supplied, a referenced record
                cannot be found or the supplied data is inconsistent.
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
            strongest_record = records_by_id[strongest_result.record_id]

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
            )

            source_query_id = self._source_query_id(query)

            return HumanitarianCase(
                source_query_id=source_query_id,
                humanitarian_summary=self._build_summary(
                    result_count=len(ordered_results),
                    strongest_result=strongest_result,
                    strongest_record=strongest_record,
                ),
                current_situation=CurrentSituation(
                    likely_event_type=(
                        strongest_record.observation.event_type
                    ),
                    reported_location=(
                        strongest_record.observation.reported_location
                    ),
                    observed_at=(
                        strongest_record.observation.observed_at
                    ),
                ),
                correlation=CaseCorrelation(
                    score=strongest_result.score,
                    evidence_level=strongest_result.confidence.value,
                    supporting_evidence=supporting_evidence,
                    conflicting_evidence=conflicting_evidence,
                    reasoning=reasoning,
                ),
                related_records=related_records,
                humanitarian_timeline=timeline,
                verification=CaseVerification(
                    status="unverified",
                    message=(
                        "This Humanitarian Case is a local probabilistic "
                        "interpretation and requires human verification."
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
        records_by_id: dict[UUID, HumanitarianRecord],
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
            record = records_by_id.get(result.record_id)

            if record is None:
                raise CorrelationProcessingError(
                    "A correlation result references a Humanitarian Record "
                    "that was not supplied to the case builder"
                )

            if result.subject_type != query.subject.type:
                raise CorrelationProcessingError(
                    "All correlation results must match the Query subject type"
                )

            if record.subject.type != query.subject.type:
                raise CorrelationProcessingError(
                    "All related Humanitarian Records must match the Query "
                    "subject type"
                )

    @staticmethod
    def _build_related_records(
        results: list[CorrelationResult],
        records_by_id: dict[UUID, HumanitarianRecord],
    ) -> list[RelatedRecord]:
        """
        Build record references in correlation-strength order.
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
        records_by_id: dict[UUID, HumanitarianRecord],
    ) -> list[TimelineEntry]:
        """
        Build an ascending chronological timeline from related records.
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
        status_group: set[CorrelationSignalStatus],
    ) -> list[EvidenceItem]:
        """
        Convert correlation signals into case-level evidence items.
        """
        evidence: list[EvidenceItem] = []

        for result in results:
            for signal in result.signals:
                if signal.status not in status_group:
                    continue

                evidence.append(
                    EvidenceItem(
                        type=self._evidence_type(signal),
                        description=signal.explanation,
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
        Convert a correlation field into a canonical evidence token.
        """
        field_token = signal.field.replace(".", "_")

        return (
            f"{field_token}_{signal.status.value}"
        )

    @staticmethod
    def _build_reasoning(
        results: list[CorrelationResult],
    ) -> str:
        """
        Build a human-readable explanation of the local interpretation.
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
            if signal.status == CorrelationSignalStatus.CONFLICT
        )

        return (
            f"The case was generated from {len(results)} correlated "
            f"Humanitarian Record candidate"
            f"{'' if len(results) == 1 else 's'}. "
            f"The strongest candidate received a correlation score of "
            f"{strongest_result.score:.2f} and an evidence level of "
            f"'{strongest_result.confidence.value}'. "
            f"The available results contain {supporting_signal_count} "
            f"supporting signal"
            f"{'' if supporting_signal_count == 1 else 's'} and "
            f"{conflicting_signal_count} conflicting signal"
            f"{'' if conflicting_signal_count == 1 else 's'}. "
            "This interpretation expresses compatibility between "
            "observations and does not establish identity."
        )

    @staticmethod
    def _build_summary(
        result_count: int,
        strongest_result: CorrelationResult,
        strongest_record: HumanitarianRecord,
    ) -> str:
        """
        Build a concise humanitarian summary from the strongest record.
        """
        location = strongest_record.observation.reported_location

        location_text = (
            f" in {location}"
            if location is not None
            else ""
        )

        return (
            f"{result_count} compatible Humanitarian Record candidate"
            f"{'' if result_count == 1 else 's'} were identified. "
            f"The strongest candidate describes a "
            f"'{strongest_record.observation.event_type}' observation"
            f"{location_text}, with a correlation score of "
            f"{strongest_result.score:.2f}. "
            "The result requires human verification."
        )

    @staticmethod
    def _source_query_id(
        query: HumanitarianQuery,
    ) -> str | None:
        """
        Read the Query identifier without imposing one particular field name.

        This permits the builder to remain compatible while the Query model
        evolves between `id` and `query_id`.
        """
        query_id = getattr(query, "query_id", None)

        if query_id is None:
            query_id = getattr(query, "id", None)

        if query_id is None:
            return None

        return str(query_id)
