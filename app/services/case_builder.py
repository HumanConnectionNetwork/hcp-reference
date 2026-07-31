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
    Build one local Humanitarian Case from correlated Humanitarian Records.

    Public clients may present the resulting object as:

    - Caso relacionado;
    - Historia del caso;
    - Reportes del caso.

    The builder organizes the interpretation around:

    1. space;
    2. time;
    3. description.

    It preserves traceability to every original Humanitarian Record and does
    not establish identity.
    """

    def build(
        self,
        query: HumanitarianQuery,
        results: list[CorrelationResult],
        records: list[HumanitarianRecord],
    ) -> HumanitarianCase:
        """
        Build one probable case history.

        The strongest correlation result provides the displayed compatibility.

        The most recent related record provides the current situation.

        Raises:
            CorrelationProcessingError:
                If no results exist or the supplied records are inconsistent.
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
                current_situation=self._build_current_situation(
                    latest_record
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
                    reasoning=self._build_reasoning(
                        results=ordered_results,
                        strongest_record=strongest_record,
                        latest_record=latest_record,
                    ),
                ),
                related_records=related_records,
                humanitarian_timeline=timeline,
                verification=CaseVerification(
                    status="unverified",
                    message=(
                        "This Humanitarian Case is a local probabilistic "
                        "interpretation built from spatial, temporal and "
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
        Order results by compatibility and evidence strength.
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
    def _build_current_situation(
        latest_record: HumanitarianRecord,
    ) -> CurrentSituation:
        """
        Build the current known situation from the latest related report.

        Structured location is preserved directly. Legacy free text remains
        available for schema 0.5 clients.
        """
        observation = (
            latest_record.observation
        )

        return CurrentSituation(
            likely_event_type=(
                observation.event_type
            ),
            declared_location=(
                observation.declared_location
            ),
            reported_location=(
                observation.reported_location
            ),
            observed_at=(
                observation.observed_at
            ),
            source_record_id=(
                latest_record.id
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
        Build compact references to reports included in the case.

        Each reference carries enough information for a public client to show:

        - event;
        - location;
        - time;
        - whether a public contact exists;
        - the record identifier needed to open the full report.
        """
        related_records: list[
            RelatedRecord
        ] = []

        for result in results:
            record = records_by_id[
                result.record_id
            ]

            observation = (
                record.observation
            )

            related_records.append(
                RelatedRecord(
                    record_id=record.id,
                    event_type=(
                        observation.event_type
                    ),
                    observed_at=(
                        observation.observed_at
                    ),
                    declared_location=(
                        observation.declared_location
                    ),
                    reported_location=(
                        observation.reported_location
                    ),
                    source=(
                        record.source_client
                    ),
                    public_contact_available=(
                        observation.public_contact
                        is not None
                    ),
                )
            )

        return related_records

    @staticmethod
    def _build_timeline(
        results: list[CorrelationResult],
        records_by_id: dict[
            UUID,
            HumanitarianRecord,
        ],
    ) -> list[TimelineEntry]:
        """
        Build the chronological history of reports included in the case.
        """
        timeline: list[
            TimelineEntry
        ] = []

        for result in results:
            record = records_by_id[
                result.record_id
            ]

            observation = (
                record.observation
            )

            timeline.append(
                TimelineEntry(
                    record_id=record.id,
                    event_type=(
                        observation.event_type
                    ),
                    observed_at=(
                        observation.observed_at
                    ),
                    declared_location=(
                        observation.declared_location
                    ),
                    reported_location=(
                        observation.reported_location
                    ),
                    description=(
                        "Humanitarian report contributed by "
                        f"{record.source_client}."
                    ),
                    public_contact_available=(
                        observation.public_contact
                        is not None
                    ),
                )
            )

        timeline.sort(
            key=lambda entry: (
                entry.observed_at
            )
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

        NOT_AVAILABLE signals are excluded because missing information is not
        a contradiction.
        """
        evidence: list[
            EvidenceItem
        ] = []

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
        Explain how the probable case history was built.
        """
        strongest_result = results[0]

        signal_counts = cls._count_signals(
            results
        )

        evidence_groups = (
            cls._count_evidence_groups(
                strongest_result.signals
            )
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

        unavailable_context = ""

        if signal_counts["unavailable"] > 0:
            unavailable_context = (
                f" {signal_counts['unavailable']} requested evidence "
                f"field"
                f"{'' if signal_counts['unavailable'] == 1 else 's'} "
                "were unavailable in the related reports."
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
            f"Across all related reports there are "
            f"{signal_counts['supporting']} supporting signal"
            f"{'' if signal_counts['supporting'] == 1 else 's'} and "
            f"{signal_counts['conflicting']} conflicting signal"
            f"{'' if signal_counts['conflicting'] == 1 else 's'}."
            f"{unavailable_context} "
            f"{location_context} "
            f"{event_context} "
            "This probable history expresses continuity between reports and "
            "does not establish identity."
        )

    @staticmethod
    def _build_summary(
        result_count: int,
        strongest_result: CorrelationResult,
        latest_record: HumanitarianRecord,
    ) -> str:
        """
        Build a concise summary centered on the latest useful report.
        """
        observation = (
            latest_record.observation
        )

        latest_location = (
            observation
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
            f"'{observation.event_type}'"
            f"{location_text} at "
            f"{observation.observed_at.isoformat()}. "
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
        Count signals by the three HCP correlation groups.
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

            elif (
                signal.field
                == "observation.search_time"
            ):
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
        Explain geographic context without claiming verified presence.
        """
        if (
            strongest_location is None
            and latest_location is None
        ):
            return (
                "No comparable declared geographic context was available."
            )

        if (
            strongest_location
            == latest_location
        ):
            return (
                "The strongest and latest reports refer to the declared "
                f"location '{latest_location}'."
            )

        if (
            strongest_location is not None
            and latest_location is not None
        ):
            return (
                f"The strongest report refers to '{strongest_location}', "
                f"while the latest report refers to '{latest_location}'. "
                "The geographic variation remains visible for human review."
            )

        available_location = (
            latest_location
            or strongest_location
        )

        return (
            "The available declared geographic context is "
            f"'{available_location}'."
        )

    @staticmethod
    def _event_context_sentence(
        strongest_record: HumanitarianRecord,
        latest_record: HumanitarianRecord,
    ) -> str:
        """
        Explain event types as situational and chronological context.
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

        if (
            strongest_event
            == latest_event
        ):
            return (
                "The latest related report describes the situation "
                f"'{latest_event}'. Event type is used only as humanitarian "
                "context."
            )

        return (
            "The strongest descriptive report describes "
            f"'{strongest_event}', while the latest report describes "
            f"'{latest_event}'. Different event types may represent "
            "different moments of the same probable case history."
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
