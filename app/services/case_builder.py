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
    - conserva candidatos con compatibilidad absoluta suficientemente alta;
    - excluye resultados débiles o espacialmente incompatibles;
    - presenta reportes relacionados para revisión humana;
    - no interpreta automáticamente que todos pertenezcan a una identidad.
    """

    # Compatibilidad mínima absoluta para mostrar un reporte relacionado.
    MIN_RELATED_SCORE = 70.0

    # El candidato principal siempre se conserva aunque quede por debajo del
    # umbral, porque representa el mejor resultado disponible.
    PRIMARY_RESULT_ALWAYS_INCLUDED = True

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

    NAME_FIELD = "subject.reported_label"
    AGE_FIELD = "subject.estimated_age"
    FEATURES_FIELD = "subject.recognition_features"

    ANIMAL_IDENTITY_FIELDS = {
        "subject.reported_label",
        "subject.species",
        "subject.size",
        "subject.breed",
        "subject.recognition_features",
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
        Construye la respuesta principal de búsqueda.

        El resultado con mayor compatibilidad se utiliza como caso principal.

        Otros resultados también se conservan cuando:

        - alcanzan una compatibilidad absoluta alta;
        - comparten el contexto espacial principal;
        - tienen suficientes señales descriptivas;
        - no dependen únicamente de una coincidencia genérica.

        Esto permite mostrar, por ejemplo, tanto Maria Rita como Maria Atencio
        cuando ambas son casos altamente compatibles con la búsqueda.
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
                query=query,
                ordered_results=ordered_results,
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
        query: HumanitarianQuery,
        ordered_results: list[CorrelationResult],
    ) -> list[CorrelationResult]:
        """
        Selecciona todos los candidatos altamente compatibles.

        Ya no se utiliza una diferencia máxima respecto al candidato principal.

        Un candidato secundario entra cuando:

        - tiene al menos MIN_RELATED_SCORE;
        - comparte evidencia espacial principal;
        - presenta suficientes datos descriptivos compatibles.

        Así, un resultado de 78% no se elimina solo porque el principal obtuvo
        92%.
        """
        strongest_result = ordered_results[0]

        selected_results = [
            strongest_result
        ]

        for result in ordered_results[1:]:
            if (
                result.score
                < self.MIN_RELATED_SCORE
            ):
                continue

            if not self._has_primary_spatial_support(
                result
            ):
                continue

            if not self._has_sufficient_subject_support(
                query=query,
                result=result,
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
        Exige compatibilidad en el contexto geográfico principal.

        Para registros HCP 0.6 se consideran:

        - país;
        - estado, provincia o región;
        - ciudad o localidad.

        El municipio y el barrio son señales secundarias y sus diferencias no
        excluyen automáticamente el resultado.

        Para registros HCP 0.5 se admite la ubicación libre compatible.
        """
        all_fields = {
            signal.field
            for signal in result.signals
        }

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

        present_primary_fields = (
            all_fields
            & cls.PRIMARY_SPATIAL_FIELDS
        )

        if not present_primary_fields:
            return False

        # Si existen las tres señales estructuradas, las tres deben apoyar el
        # candidato.
        if (
            present_primary_fields
            == cls.PRIMARY_SPATIAL_FIELDS
        ):
            return (
                cls.PRIMARY_SPATIAL_FIELDS
                <= supporting_fields
            )

        # Compatibilidad defensiva con consultas o registros parcialmente
        # estructurados.
        return len(
            present_primary_fields
            & supporting_fields
        ) >= 2

    @classmethod
    def _has_sufficient_subject_support(
        cls,
        query: HumanitarianQuery,
        result: CorrelationResult,
    ) -> bool:
        """
        Exige más que una coincidencia descriptiva genérica.

        Personas:
        - nombre compatible; y
        - edad o características compatibles cuando fueron consultadas.

        Animales:
        - al menos dos señales entre nombre, especie, raza, tamaño y
          características.

        Esto evita que una sola palabra común incorpore registros irrelevantes.
        """
        supporting_fields = {
            signal.field
            for signal in result.signals
            if signal.status
            in cls.SUPPORTING_STATUSES
        }

        if query.subject.type == "animal":
            animal_support_count = len(
                supporting_fields
                & cls.ANIMAL_IDENTITY_FIELDS
            )

            return animal_support_count >= 2

        query_has_name = bool(
            getattr(
                query.subject,
                "reported_label",
                None,
            )
        )

        query_has_age = (
            getattr(
                query.subject,
                "estimated_age",
                None,
            )
            is not None
        )

        query_has_features = bool(
            getattr(
                query.subject,
                "recognition_features",
                None,
            )
        )

        name_supported = (
            cls.NAME_FIELD
            in supporting_fields
        )

        age_supported = (
            cls.AGE_FIELD
            in supporting_fields
        )

        features_supported = (
            cls.FEATURES_FIELD
            in supporting_fields
        )

        if query_has_name and not name_supported:
            return False

        secondary_requested = (
            query_has_age
            or query_has_features
        )

        if not secondary_requested:
            return name_supported

        secondary_supported = (
            (
                query_has_age
                and age_supported
            )
            or (
                query_has_features
                and features_supported
            )
        )

        return (
            name_supported
            and secondary_supported
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
        Devuelve el reporte seleccionado más reciente.

        La situación probable se deriva del reporte más reciente dentro del
        conjunto de casos altamente compatibles.
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
        Construye referencias a todos los reportes altamente compatibles.
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
        Construye la lista cronológica de reportes altamente compatibles.

        En esta etapa, la lista puede contener más de un posible caso. La
        interfaz debe presentar cada reporte con acceso independiente mediante
        su ID y no afirmar que todos representan una sola identidad.
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
        Convierte señales de los reportes seleccionados en evidencia explicable.
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
        Explica la selección por compatibilidad absoluta.
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
            f"{len(selected_results)} report"
            f"{'' if len(selected_results) == 1 else 's'} "
            "were retained because they reached the absolute compatibility "
            "threshold and shared sufficient spatial and descriptive "
            "evidence. "
            f"The strongest result received a compatibility score of "
            f"{strongest_result.score:.2f} and an evidence strength level "
            f"of '{strongest_result.confidence.value}'. "
            f"The retained results contain {supporting_signal_count} "
            f"supporting signal"
            f"{'' if supporting_signal_count == 1 else 's'} and "
            f"{conflicting_signal_count} conflicting signal"
            f"{'' if conflicting_signal_count == 1 else 's'}. "
            f"{excluded_count} weaker or structurally incompatible candidate"
            f"{'' if excluded_count == 1 else 's'} "
            "were excluded. "
            "The retained reports are possible related cases and must not be "
            "assumed to represent one confirmed identity."
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
        Resume el conjunto de casos altamente compatibles.
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

        case_word = (
            "case"
            if len(selected_results) == 1
            else "cases"
        )

        return (
            f"{len(selected_results)} highly compatible related {case_word} "
            "were identified. "
            f"The strongest report{subject_text} describes a "
            f"'{strongest_record.observation.event_type}' situation"
            f"{location_text}, with a compatibility score of "
            f"{strongest_result.score:.2f}. "
            "Each report must be reviewed independently and the result "
            "requires human verification."
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
