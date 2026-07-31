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

    A Humanitarian Case is a probable history assembled from compatible
    reports. It is not a confirmed identity and does not replace the
    original Humanitarian Records.

    The builder organizes the result around:

    1. space;
    2. time;
    3. description.

    It preserves all related reports and presents the most recent report as
    the current known situation.

    This service does not:

    - confirm identity;
    - modify or merge source records;
    - hide spatial or temporal conflicts;
    - infer why a person or animal moved;
    - persist the resulting case;
    - synchronize the case between HCP Nodes.
    """

    def build(
        self,
        query: HumanitarianQuery,
        results: list[CorrelationResult],
        records: list[HumanitarianRecord],
    ) -> HumanitarianCase:
        """
        Build one local Humanitarian Case.

        The strongest result determines the displayed compatibility score.

        The latest related record determines the current situation because
        event types may describe successive moments of one humanitarian
        history, for example:

        missing
        → hospitalized
        → sheltered
        → safe.

        Raises:
            CorrelationProcessingError:
                If the supplied results and records are inconsistent.
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

            ordered_results = self._order_results(
                results
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
                        .location_display_text()
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
                        "interpretation built from space, time and "
                        "descriptive compatibility. It does not establish "
                        "identity and requires human verification."
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
    def _order_results(
        results: list[CorrelationResult],
    ) -> list[CorrelationResult]:
        """
        Order correlation results from strongest to weakest.

        HumanitarianCase currently receives the final score and confidence
        produced by CorrelationService. Chronological ordering is handled
        separately by the timeline builder.
        """
        return sorted(
            results,
            key=lambda result: (
                result.score,
                HumanitarianCaseBuilder
                ._confidence_sort_value(
                    result.confidence.value
                ),
            ),
            reverse=True,
        )

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

        Event type is used only to describe the most recent known report.
        It does not participate as identity evidence.
        """
        related_records = [
            records_by_id[result.record_id]
            for result in results
        ]

        if not related_records:
            raise CorrelationProcessingError(
                "Unable to determine the latest related report"
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
        Build references to the reports participating in the case.

        Related records remain ordered by compatibility. The timeline provides
        the chronological history.
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
        Build the chronological history of the related case.

        During the 0.5 to 0.6 transition, TimelineEntry still exposes
        reported_location as display text. Structured DeclaredLocation remains
        stored in the original Humanitarian Record and will be added directly
        to the HumanitarianCase model in the next step.
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
                    ]
                    .observation
                    .location_display_text()
                ),
                description=(
                    "Humanitarian report contributed by "
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
        Convert correlation signals into case-level evidence.

        Evidence remains traceable to the original Humanitarian Record.

        NOT_AVAILABLE signals are not included as conflicts because absence of
        information is not a contradiction.
        """
        evidence: list[EvidenceItem] = []

        for result in results:
            for signal in result.signals:
                if signal.status not in status_group:
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
        Convert one correlation signal into a canonical evidence token.
        """
        field_token = (
            signal.field
            .replace(
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
        Explain how the case was constructed.

        The explanation separates:

        - spatial evidence;
        - temporal evidence;
        - descriptive evidence;
        - conflicts;
        - missing information;
        - the latest known report.
        """
        strongest_result = results[0]

        signal_counts = cls._count_signals(
            results
        )

        evidence_groups = cls._count_evidence_groups(
            strongest_result.signals
        )

        strongest_location = (
            strongest_record
            .observation
            .location_display_text()
        )

        latest_location = (
            latest_record
            .observation
            .location_display_text()
        )

        location_context = (
            cls._location_context_sentence(
                strongest_location=strongest_location,
                latest_location=latest_location,
            )
        )

        event_context = (
            cls._event_context_sentence(
                strongest_record=strongest_record,
                latest_record=latest_record,
            )
        )

        unavailable_context = (
            f" {signal_counts['unavailable']} requested evidence "
            f"field"
            f"{'' if signal_counts['unavailable'] == 1 else 's'} "
            "were unavailable in the related reports."
            if signal_counts["unavailable"] > 0
            else ""
        )

        return (
            f"The case was built from {len(results)} related Humanitarian "
            f"Record"
            f"{'' if len(results) == 1 else 's'}. "
            f"The strongest report received a compatibility score of "
            f"{strongest_result.score:.2f} and an evidence strength level "
            f"of '{strongest_result.confidence.value}'. "
            f"The strongest result contains "
            f"{evidence_groups['space']} spatial, "
            f"{evidence_groups['time']} temporal and "
            f"{evidence_groups['description']} descriptive signal"
            f"{'' if evidence_groups['description'] == 1 else 's'}. "
            f"Across the related reports there are "
            f"{signal_counts['supporting']} supporting signal"
            f"{'' if signal_counts['supporting'] == 1 else 's'} and "
            f"{signal_counts['conflicting']} conflicting signal"
            f"{'' if signal_counts['conflicting'] == 1 else 's'}."
            f"{unavailable_context} "
            f"{location_context} "
            f"{event_context} "
            "The case expresses probable continuity between reports and "
            "does not establish identity."
        )

    @classmethod
    def _build_summary(
        cls,
        result_count: int,
        strongest_result: CorrelationResult,
        latest_record: HumanitarianRecord,
    ) -> str:
        """
        Build a concise summary focused on the latest useful information.

        The frontend will later present this as a public 'Caso relacionado'.
        """
        latest_location = (
            latest_record
            .observation
            .location_display_text()
        )

        location_text = (
            f" in {latest_location}"
            if latest_location
            else ""
        )

        return (
            f"{result_count} related Humanitarian Record"
            f"{'' if result_count == 1 else 's'} were identified. "
            f"The strongest report has a compatibility score of "
            f"{strongest_result.score:.2f}. "
            f"The latest known report describes "
            f"'{latest_record.observation.event_type}'"
            f"{location_text} at "
            f"{latest_record.observation.observed_at.isoformat()}. "
            "This probable case history requires human verification."
        )

    @staticmethod
    def _count_signals(
        results: list[CorrelationResult],
    ) -> dict[str, int]:
        """
        Count supporting, conflicting and unavailable signals.
        """
        supporting = 0
        conflicting = 0
        unavailable = 0

        for result in results:
            for signal in result.signals:
                if signal.status in {
                    CorrelationSignalStatus.MATCH,
                    CorrelationSignalStatus.PARTIAL_MATCH,
                }:
                    supporting += 1

                elif (
                    signal.status
                    == CorrelationSignalStatus.CONFLICT
                ):
                    conflicting += 1

                elif (
                    signal.status
                    == CorrelationSignalStatus.NOT_AVAILABLE
                ):
                    unavailable += 1

        return {
            "supporting": supporting,
            "conflicting": conflicting,
            "unavailable": unavailable,
        }

    @staticmethod
    def _count_evidence_groups(
        signals: list[CorrelationSignal],
    ) -> dict[str, int]:
        """
        Count evidence signals by the three HCP correlation groups.
        """
        space = 0
        time = 0
        description = 0

        for signal in signals:
            if signal.field in {
                "observation.declared_location",
                "observation.reported_location",
            }:
                space += 1

            elif signal.field == "observation.search_time":
                time += 1

            elif signal.field.startswith(
                "subject."
            ):
                description += 1

        return {
            "space": space,
            "time": time,
            "description": description,
        }

    @staticmethod
    def _location_context_sentence(
        strongest_location: str | None,
        latest_location: str | None,
    ) -> str:
        """
        Explain the geographic context without claiming physical presence.
        """
        if (
            strongest_location is None
            and latest_location is None
        ):
            return (
                "No comparable geographic context was available for the "
                "case summary."
            )

        if strongest_location == latest_location:
            return (
                f"The strongest and latest reports refer to the declared "
                f"location '{latest_location}'."
            )

        if (
            strongest_location is not None
            and latest_location is not None
        ):
            return (
                f"The strongest report refers to '{strongest_location}', "
                f"while the latest report refers to '{latest_location}'. "
                "This geographic variation remains visible for human review."
            )

        available_location = (
            latest_location
            or strongest_location
        )

        return (
            f"The available declared geographic context is "
            f"'{available_location}'."
        )

    @staticmethod
    def _event_context_sentence(
        strongest_record: HumanitarianRecord,
        latest_record: HumanitarianRecord,
    ) -> str:
        """
        Explain that event types form context and chronology.

        Event types do not support or contradict identity compatibility.
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
                f"The latest related report describes the situation "
                f"'{latest_event}'. Event type is used only as humanitarian "
                "context."
            )

        return (
            f"The strongest descriptive report describes "
            f"'{strongest_event}', while the latest related report describes "
            f"'{latest_event}'. Different event types may represent different "
            "moments in the same probable case history."
        )

    @staticmethod
    def _source_query_id(
        query: HumanitarianQuery,
    ) -> str | None:
        """
        Return the optional Query identifier.
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

    @staticmethod
    def _confidence_sort_value(
        value: str,
    ) -> int:
        """
        Convert evidence-level labels into a deterministic sorting value.
        """
        values = {
            "very_low": 0,
            "low": 1,
            "moderate": 2,
            "medium": 2,
            "high": 3,
            "very_high": 4,
        }

        return values.get(
            value,
            0,
        )
