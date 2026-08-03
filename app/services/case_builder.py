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
    Construye una interpretación humanitaria local a partir de resultados
    de correlación.

    Un Humanitarian Case:

    - no confirma identidad;
    - no modifica los registros originales;
    - no mezcla automáticamente todos los candidatos;
    - contiene únicamente registros suficientemente compatibles entre sí;
    - presenta una secuencia temporal local para revisión humana.
    """

    # Un candidato secundario debe permanecer cerca del principal.
    MAX_SCORE_GAP = 12.0

    # Un registro secundario débil no debe formar parte de la misma historia.
    MIN_RELATED_SCORE = 60.0

    DESCRIPTIVE_FIELDS = {
        "subject.reported_label",
        "subject.estimated_age",
        "subject.recognition_features",
        "subject.species",
        "subject.size",
        "subject.breed",
    }

    PRIMARY_SPATIAL_FIELDS = {
        "observation.declared_location.country_code",
        "observation.declared_location.admin_level_1",
        "observation.declared_location.locality",
    }

    SECONDARY_SPATIAL_FIELDS = {
        "observation.declared_location.admin_level_2",
        "observation.declared_location.district",
        "observation.reported_location",
    }

    SUPPORTING_STATUSES = {
        CorrelationSignalStatus.MATCH,
        CorrelationSignalStatus.PARTIAL_MATCH,
    }

    def build(
        self,
        query: HumanitarianQuery,
        results: list[CorrelationResult],
        records: list[HumanitarianRecord],
    ) -> HumanitarianCase:
        """
        Construye un caso principal con registros realmente relacionados.

        El resultado de mayor puntuación se utiliza como candidato principal.
        Los demás resultados solo se incorporan cuando:

        - superan el umbral mínimo;
        - están suficientemente cerca del candidato principal;
        - comparten contexto espacial;
        - aportan al menos una señal descriptiva útil.
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
                key=lambda result: (
                    result.score,
                    self._supporting_signal_count(result),
                ),
                reverse=True,
            )

            self._validate_inputs(
                query=query,
                results=ordered_results,
                records_by_id=records_by_id,
            )

            selected_results = self._select_case_results(
                ordered_results
            )

            strongest_result = selected_results[0]
            strongest_record = records_by_id[
                strongest_result.record_id
            ]

            latest_record = self._latest_record(
                results=selected_results,
                records_by_id=records_by_id,
            )

            related_records = self._build_related_records(
                results=selected_results,
                records_by_id=records_by_id,
            )

            timeline = self._build_timeline(
                results=selected_results,
                records_by_id=records_by_id,
            )

            supporting_evidence = self._build_evidence(
                results=selected_results,
                status_group=self.SUPPORTING_STATUSES,
            )

            conflicting_evidence = self._build_evidence(
                results=selected_results,
                status_group={
                    CorrelationSignalStatus.CONFLICT,
                },
            )

            reasoning = self._build_reasoning(
                all_result_count=len(ordered_results),
                selected_results=selected_results,
            )

            return HumanitarianCase(
                source_query_id=self._source_query_id(query),
                humanitarian_summary=self._build_summary(
                    selected_results=selected_results,
                    strongest_result=strongest_result,
                    strongest_record=strongest_record,
                ),
                current_situation=CurrentSituation(
                    likely_event_type=(
                        latest_record.observation.event_type
                    ),
                    reported_location=(
                        self._record_location_text(
                            latest_record
                        )
                    ),
                    observed_at=(
                        latest_record.observation.observed_at
                    ),
                ),
                correlation=CaseCorrelation(
                    score=strongest_result.score,
                    evidence_level=(
                        strongest_result.confidence.value
                    ),
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

    def _select_case_results(
        self,
        ordered_results: list[CorrelationResult],
    ) -> list[CorrelationResult]:
        """
        Selecciona los registros que realmente forman la historia principal.

        El mejor candidato siempre se conserva.

        Un candidato adicional debe:

        - tener al menos 60 puntos;
        - estar a no más de 12 puntos del principal;
        - compartir evidencia espacial principal;
        - aportar una señal descriptiva compatible.

        Esto evita que varios registros lejanos o genéricos sean presentados
        como si pertenecieran automáticamente a una misma persona o animal.
        """
        strongest_result = ordered_results[0]

        selected_results = [
            strongest_result
        ]

        for result in ordered_results[1:]:
            score_gap = (
                strongest_result.score
                - result.score
            )

            if (
                result.score
                < self.MIN_RELATED_SCORE
            ):
                continue

            if (
                score_gap
                > self.MAX_SCORE_GAP
            ):
                continue

            if not self._has_primary_spatial_support(
                result
            ):
                continue

            if not self._has_descriptive_support(
                result
            ):
                continue

            selected_results.append(
                result
            )

        return selected_results

    @classmethod
    def _has_primary_spatial_support(
        cls,
        result: CorrelationResult,
    ) -> bool:
        """
        Exige compatibilidad geográfica estructural.

        Para registros HCP 0.6 se requiere evidencia compatible en:

        - país;
        - estado, provincia o región;
        - ciudad o localidad.

        Para registros antiguos 0.5 se acepta una ubicación libre compatible.
        """
        supporting_fields = {
            signal.field
            for signal in result.signals
            if signal.status
            in cls.SUPPORTING_STATUSES
        }

        if (
            "observation.reported_location"
            in supporting_fields
        ):
            return True

        required_fields_present = (
            cls.PRIMARY_SPATIAL_FIELDS
            & {
                signal.field
                for signal in result.signals
            }
        )

        # Si la consulta realmente produjo las tres señales espaciales,
        # deben ser compatibles para formar parte de la misma historia.
        if (
            required_fields_present
            == cls.PRIMARY_SPATIAL_FIELDS
        ):
            return (
                cls.PRIMARY_SPATIAL_FIELDS
                <= supporting_fields
            )

        # Compatibilidad defensiva con consultas parciales o modelos antiguos.
        return len(
            supporting_fields
            & cls.PRIMARY_SPATIAL_FIELDS
        ) >= 2

    @classmethod
    def _has_descriptive_support(
        cls,
        result: CorrelationResult,
    ) -> bool:
        """
        Requiere al menos una evidencia descriptiva positiva.

        La coincidencia temporal o espacial por sí sola no es suficiente para
        incorporar un registro a la historia principal.
        """
        return any(
            signal.field
            in cls.DESCRIPTIVE_FIELDS
            and signal.status
            in cls.SUPPORTING_STATUSES
            for signal in result.signals
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
        Valida consistencia entre consulta, resultados y registros.
        """
        result_ids = [
            result.record_id
            for result in results
        ]

        if (
            len(result_ids)
            != len(set(result_ids))
        ):
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
        Devuelve el registro relacionado más reciente.

        La situación actual probable debe derivarse del registro más reciente,
        no necesariamente del registro con mayor compatibilidad.
        """
        return max(
            (
                records_by_id[
                    result.record_id
                ]
                for result in results
            ),
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
        Construye referencias únicamente a los registros seleccionados.
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

    @classmethod
    def _build_timeline(
        cls,
        results: list[CorrelationResult],
        records_by_id: dict[
            UUID,
            HumanitarianRecord,
        ],
    ) -> list[TimelineEntry]:
        """
        Construye la Historia del caso en orden cronológico.

        La ubicación estructurada HCP 0.6 se convierte temporalmente en texto
        porque TimelineEntry todavía expone `reported_location`.
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
                    cls._record_location_text(
                        records_by_id[
                            result.record_id
                        ]
                    )
                ),
                description=(
                    "Humanitarian report contributed by "
                    f"{records_by_id[result.record_id].source_client}."
                ),
            )
            for result in results
        ]

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
        Convierte señales seleccionadas en evidencia explicable del caso.

        Solo se incluye evidencia perteneciente a los registros que realmente
        forman la historia principal.
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
        Convierte el campo comparado en un token canónico.
        """
        field_token = (
            signal.field
            .replace(".", "_")
        )

        return (
            f"{field_token}_"
            f"{signal.status.value}"
        )

    @classmethod
    def _build_reasoning(
        cls,
        all_result_count: int,
        selected_results: list[
            CorrelationResult
        ],
    ) -> str:
        """
        Explica por qué algunos candidatos entraron en la historia y otros no.
        """
        strongest_result = (
            selected_results[0]
        )

        supporting_signal_count = sum(
            1
            for result in selected_results
            for signal in result.signals
            if signal.status
            in cls.SUPPORTING_STATUSES
        )

        conflicting_signal_count = sum(
            1
            for result in selected_results
            for signal in result.signals
            if signal.status
            == CorrelationSignalStatus.CONFLICT
        )

        excluded_count = (
            all_result_count
            - len(selected_results)
        )

        return (
            f"The local search evaluated {all_result_count} correlated "
            f"candidate"
            f"{'' if all_result_count == 1 else 's'}. "
            f"{len(selected_results)} record"
            f"{'' if len(selected_results) == 1 else 's'} "
            "were retained in the primary Humanitarian Case because they "
            "shared sufficiently close spatial and descriptive evidence. "
            f"The strongest candidate received a compatibility score of "
            f"{strongest_result.score:.2f} and an evidence strength level "
            f"of '{strongest_result.confidence.value}'. "
            f"The selected case contains {supporting_signal_count} "
            f"supporting signal"
            f"{'' if supporting_signal_count == 1 else 's'} and "
            f"{conflicting_signal_count} conflicting signal"
            f"{'' if conflicting_signal_count == 1 else 's'}. "
            f"{excluded_count} weaker or structurally different candidate"
            f"{'' if excluded_count == 1 else 's'} "
            "were not incorporated into this case history. "
            "This interpretation expresses compatibility and does not "
            "establish identity."
        )

    @classmethod
    def _build_summary(
        cls,
        selected_results: list[
            CorrelationResult
        ],
        strongest_result: CorrelationResult,
        strongest_record: HumanitarianRecord,
    ) -> str:
        """
        Resume el caso principal desde el registro más compatible.
        """
        location = (
            cls._record_location_text(
                strongest_record
            )
        )

        location_text = (
            f" in {location}"
            if location
            else ""
        )

        subject_label = (
            strongest_record
            .subject
            .reported_label
        )

        subject_text = (
            f" describing '{subject_label}'"
            if subject_label
            else ""
        )

        return (
            f"A primary related case was identified from "
            f"{len(selected_results)} Humanitarian Record"
            f"{'' if len(selected_results) == 1 else 's'}. "
            f"The strongest report{subject_text} describes a "
            f"'{strongest_record.observation.event_type}' situation"
            f"{location_text}, with a compatibility score of "
            f"{strongest_result.score:.2f}. "
            "The result requires human verification."
        )

    @staticmethod
    def _record_location_text(
        record: HumanitarianRecord,
    ) -> str | None:
        """
        Devuelve una ubicación legible para registros HCP 0.6 y 0.5.
        """
        declared_location = getattr(
            record.observation,
            "declared_location",
            None,
        )

        if declared_location is not None:
            values = [
                getattr(
                    declared_location,
                    "district",
                    None,
                ),
                getattr(
                    declared_location,
                    "locality",
                    None,
                ),
                getattr(
                    declared_location,
                    "admin_level_2",
                    None,
                ),
                getattr(
                    declared_location,
                    "admin_level_1",
                    None,
                ),
                getattr(
                    declared_location,
                    "country_code",
                    None,
                ),
            ]

            normalized_values: list[str] = []

            for value in values:
                if value is None:
                    continue

                normalized_value = (
                    str(value).strip()
                )

                if not normalized_value:
                    continue

                # Evita duplicados consecutivos como:
                # Cabimas, Cabimas, Zulia, VE.
                if (
                    normalized_values
                    and normalized_values[-1].casefold()
                    == normalized_value.casefold()
                ):
                    continue

                normalized_values.append(
                    normalized_value
                )

            if normalized_values:
                return ", ".join(
                    normalized_values
                )

        legacy_location = getattr(
            record.observation,
            "reported_location",
            None,
        )

        if legacy_location is None:
            return None

        normalized_legacy = (
            str(legacy_location).strip()
        )

        return (
            normalized_legacy
            or None
        )

    @staticmethod
    def _supporting_signal_count(
        result: CorrelationResult,
    ) -> int:
        return sum(
            1
            for signal in result.signals
            if signal.status
            in {
                CorrelationSignalStatus.MATCH,
                CorrelationSignalStatus.PARTIAL_MATCH,
            }
        )

    @staticmethod
    def _source_query_id(
        query: HumanitarianQuery,
    ) -> str | None:
        """
        Lee el identificador opcional de la consulta.
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
