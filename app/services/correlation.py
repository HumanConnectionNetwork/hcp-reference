from datetime import datetime
from difflib import SequenceMatcher
import unicodedata

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
    Servicio local de correlación explicable.

    Compara una Humanitarian Query con registros candidatos previamente
    seleccionados por SearchService.

    La correlación utiliza:

    - nombre o etiqueta;
    - edad aproximada;
    - características para reconocer;
    - país;
    - estado, provincia o región;
    - municipio o división local;
    - ciudad o localidad;
    - barrio, sector o urbanización;
    - relación temporal entre el reporte y la búsqueda;
    - datos específicos de animales.

    event_type no participa como evidencia de identidad. Solo describe la
    situación observada en cada reporte.

    El resultado expresa compatibilidad y nunca confirma identidad.
    """

    # Evidencia descriptiva común.
    REPORTED_LABEL_WEIGHT = 25.0
    ESTIMATED_AGE_WEIGHT = 15.0
    RECOGNITION_FEATURES_WEIGHT = 20.0

    # Evidencia espacial.
    COUNTRY_WEIGHT = 10.0
    ADMIN_LEVEL_1_WEIGHT = 10.0
    ADMIN_LEVEL_2_WEIGHT = 5.0
    LOCALITY_WEIGHT = 10.0
    DISTRICT_WEIGHT = 3.0

    # Evidencia temporal.
    TEMPORAL_WEIGHT = 2.0

    # Evidencia específica de animales.
    SPECIES_WEIGHT = 12.0
    ANIMAL_SIZE_WEIGHT = 4.0
    BREED_WEIGHT = 8.0

    # Tolerancias descriptivas.
    STRONG_TEXT_SIMILARITY = 0.85
    PARTIAL_TEXT_SIMILARITY = 0.45

    # Una búsqueda por un nombre contenido en otro debe considerarse útil:
    # "Maria" frente a "Maria Atencio".
    CONTAINMENT_SIMILARITY_FLOOR = 0.82

    def correlate_records(
        self,
        query: HumanitarianQuery,
        records: list[HumanitarianRecord],
        limit: int | None = None,
        minimum_score: float = 0.0,
    ) -> list[CorrelationResult]:
        """
        Correlaciona una consulta con los registros candidatos.

        Los resultados se devuelven de mayor a menor compatibilidad.

        SearchService debe haber eliminado previamente registros claramente
        incompatibles por tipo de sujeto y contexto geográfico.
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
                key=lambda result: (
                    result.score,
                    self._supporting_signal_count(result),
                ),
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
        Correlaciona una consulta con un único registro.

        Solo participan en el porcentaje los datos efectivamente declarados
        en la consulta.

        Cuando el registro no contiene un dato solicitado, se añade una señal
        NOT_AVAILABLE. Esto reduce la fuerza de evidencia, pero no se presenta
        como una contradicción.
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
            description="reported name or label",
            containment_floor=self.CONTAINMENT_SIMILARITY_FLOOR,
        )

        if query.subject.type == "human":
            self._append_age_signal(
                signals=signals,
                query_age=getattr(
                    query.subject,
                    "estimated_age",
                    None,
                ),
                record_age=getattr(
                    record.subject,
                    "estimated_age",
                    None,
                ),
            )

        self._append_text_signal(
            signals=signals,
            field="subject.recognition_features",
            query_value=query.subject.recognition_features,
            record_value=record.subject.recognition_features,
            weight=self.RECOGNITION_FEATURES_WEIGHT,
            description="recognition features",
            containment_floor=0.55,
        )

        if query.subject.type == "animal":
            self._append_animal_signals(
                signals=signals,
                query=query,
                record=record,
            )

        self._append_location_signals(
            signals=signals,
            query=query,
            record=record,
        )

        self._append_temporal_signal(
            signals=signals,
            query=query,
            record=record,
        )

        score = self._calculate_normalized_score(
            signals
        )

        evidence_strength = self._calculate_evidence_strength(
            signals
        )

        return CorrelationResult(
            record_id=record.id,
            subject_type=record.subject.type,
            score=score,
            confidence=confidence_from_score(
                evidence_strength
            ),
            signals=signals,
        )

    def _append_animal_signals(
        self,
        signals: list[CorrelationSignal],
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> None:
        """
        Añade evidencia específica de animales.
        """
        self._append_text_signal(
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
            weight=self.SPECIES_WEIGHT,
            description="species",
            containment_floor=0.80,
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

        self._append_text_signal(
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
            weight=self.BREED_WEIGHT,
            description="breed",
            containment_floor=0.72,
        )

    def _append_location_signals(
        self,
        signals: list[CorrelationSignal],
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> None:
        """
        Compara la ubicación declarada jerárquicamente.

        País, región y localidad son señales estructurales fuertes.

        Una diferencia de barrio dentro de la misma ciudad no excluye el
        registro. Se conserva como compatibilidad parcial porque una persona
        o animal puede desplazarse dentro de la misma localidad.
        """
        query_observation = getattr(
            query,
            "observation",
            None,
        )

        if query_observation is None:
            return

        query_location = getattr(
            query_observation,
            "declared_location",
            None,
        )

        if query_location is None:
            return

        record_location = getattr(
            record.observation,
            "declared_location",
            None,
        )

        if record_location is None:
            self._append_legacy_location_signal(
                signals=signals,
                query_location=query_location,
                record=record,
            )
            return

        self._append_country_signal(
            signals=signals,
            query_value=getattr(
                query_location,
                "country_code",
                None,
            ),
            record_value=getattr(
                record_location,
                "country_code",
                None,
            ),
        )

        self._append_location_text_signal(
            signals=signals,
            field="observation.declared_location.admin_level_1",
            query_value=getattr(
                query_location,
                "admin_level_1",
                None,
            ),
            record_value=getattr(
                record_location,
                "admin_level_1",
                None,
            ),
            weight=self.ADMIN_LEVEL_1_WEIGHT,
            description="state, province or region",
            difference_is_partial=False,
        )

        self._append_location_text_signal(
            signals=signals,
            field="observation.declared_location.admin_level_2",
            query_value=getattr(
                query_location,
                "admin_level_2",
                None,
            ),
            record_value=getattr(
                record_location,
                "admin_level_2",
                None,
            ),
            weight=self.ADMIN_LEVEL_2_WEIGHT,
            description="municipality or local division",
            difference_is_partial=True,
        )

        self._append_location_text_signal(
            signals=signals,
            field="observation.declared_location.locality",
            query_value=getattr(
                query_location,
                "locality",
                None,
            ),
            record_value=getattr(
                record_location,
                "locality",
                None,
            ),
            weight=self.LOCALITY_WEIGHT,
            description="city or locality",
            difference_is_partial=False,
        )

        self._append_location_text_signal(
            signals=signals,
            field="observation.declared_location.district",
            query_value=getattr(
                query_location,
                "district",
                None,
            ),
            record_value=getattr(
                record_location,
                "district",
                None,
            ),
            weight=self.DISTRICT_WEIGHT,
            description="district, neighborhood or sector",
            difference_is_partial=True,
        )

    def _append_country_signal(
        self,
        signals: list[CorrelationSignal],
        query_value: str | None,
        record_value: str | None,
    ) -> None:
        """
        Compara códigos de país ISO sin distinguir mayúsculas.
        """
        if query_value is None:
            return

        if record_value is None:
            signals.append(
                CorrelationSignal(
                    field=(
                        "observation.declared_location."
                        "country_code"
                    ),
                    status=(
                        CorrelationSignalStatus
                        .NOT_AVAILABLE
                    ),
                    contribution=0.0,
                    explanation=(
                        "The candidate record does not contain declared "
                        "country evidence."
                    ),
                    query_value=query_value,
                    record_value=None,
                )
            )
            return

        query_code = query_value.strip().upper()
        record_code = record_value.strip().upper()

        if query_code == record_code:
            status = CorrelationSignalStatus.MATCH
            contribution = self.COUNTRY_WEIGHT
            explanation = (
                "The declared countries are equal."
            )
        else:
            status = CorrelationSignalStatus.CONFLICT
            contribution = 0.0
            explanation = (
                "The declared countries are different."
            )

        signals.append(
            CorrelationSignal(
                field=(
                    "observation.declared_location."
                    "country_code"
                ),
                status=status,
                contribution=round(
                    contribution,
                    2,
                ),
                explanation=explanation,
                query_value=query_code,
                record_value=record_code,
            )
        )

    def _append_location_text_signal(
        self,
        signals: list[CorrelationSignal],
        field: str,
        query_value: str | None,
        record_value: str | None,
        weight: float,
        description: str,
        difference_is_partial: bool,
    ) -> None:
        """
        Compara un nivel geográfico declarado.

        Para municipio y barrio, una diferencia no se trata automáticamente
        como incompatibilidad total cuando los niveles superiores coinciden.
        """
        if query_value is None:
            return

        if record_value is None:
            signals.append(
                CorrelationSignal(
                    field=field,
                    status=(
                        CorrelationSignalStatus
                        .NOT_AVAILABLE
                    ),
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
            containment_floor=0.80,
        )

        if similarity >= 0.85:
            status = CorrelationSignalStatus.MATCH
            contribution = weight * similarity
            explanation = (
                f"The declared {description} values are strongly "
                f"compatible ({round(similarity * 100)}% similarity)."
            )

        elif similarity >= 0.55:
            status = (
                CorrelationSignalStatus
                .PARTIAL_MATCH
            )
            contribution = weight * similarity
            explanation = (
                f"The declared {description} values are partially "
                f"compatible ({round(similarity * 100)}% similarity)."
            )

        elif difference_is_partial:
            status = (
                CorrelationSignalStatus
                .PARTIAL_MATCH
            )
            contribution = weight * 0.20
            explanation = (
                f"The declared {description} values are different, but "
                "this local difference does not exclude the candidate."
            )

        else:
            status = CorrelationSignalStatus.CONFLICT
            contribution = 0.0
            explanation = (
                f"The declared {description} values are not compatible "
                f"({round(similarity * 100)}% similarity)."
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

    def _append_legacy_location_signal(
        self,
        signals: list[CorrelationSignal],
        query_location: object,
        record: HumanitarianRecord,
    ) -> None:
        """
        Mantiene compatibilidad básica con registros HCP 0.5.
        """
        legacy_location = getattr(
            record.observation,
            "reported_location",
            None,
        )

        query_location_text = self._format_location(
            query_location
        )

        self._append_text_signal(
            signals=signals,
            field="observation.reported_location",
            query_value=query_location_text,
            record_value=legacy_location,
            weight=(
                self.ADMIN_LEVEL_1_WEIGHT
                + self.LOCALITY_WEIGHT
            ),
            description="legacy reported location",
            containment_floor=0.72,
        )

    def _append_temporal_signal(
        self,
        signals: list[CorrelationSignal],
        query: HumanitarianQuery,
        record: HumanitarianRecord,
    ) -> None:
        """
        Compara el momento de búsqueda con el momento del reporte.

        El tiempo aporta contexto y posibilidad de desplazamiento. Un reporte
        antiguo no se convierte automáticamente en contradicción.
        """
        query_observation = getattr(
            query,
            "observation",
            None,
        )

        if query_observation is None:
            return

        searched_at = getattr(
            query_observation,
            "searched_at",
            None,
        )

        observed_at = getattr(
            record.observation,
            "observed_at",
            None,
        )

        if searched_at is None:
            return

        if observed_at is None:
            signals.append(
                CorrelationSignal(
                    field="observation.temporal_distance",
                    status=(
                        CorrelationSignalStatus
                        .NOT_AVAILABLE
                    ),
                    contribution=0.0,
                    explanation=(
                        "The candidate record does not contain observation "
                        "time evidence."
                    ),
                    query_value=self._datetime_text(
                        searched_at
                    ),
                    record_value=None,
                )
            )
            return

        searched_datetime = self._as_datetime(
            searched_at
        )
        observed_datetime = self._as_datetime(
            observed_at
        )

        if (
            searched_datetime is None
            or observed_datetime is None
        ):
            signals.append(
                CorrelationSignal(
                    field="observation.temporal_distance",
                    status=(
                        CorrelationSignalStatus
                        .NOT_AVAILABLE
                    ),
                    contribution=0.0,
                    explanation=(
                        "The temporal distance could not be calculated."
                    ),
                    query_value=self._datetime_text(
                        searched_at
                    ),
                    record_value=self._datetime_text(
                        observed_at
                    ),
                )
            )
            return

        difference_seconds = (
            searched_datetime
            - observed_datetime
        ).total_seconds()

        # Un reporte posterior al momento de búsqueda no se utiliza como una
        # señal positiva fuerte, pero tampoco invalida automáticamente el
        # resto de la evidencia.
        if difference_seconds < 0:
            status = (
                CorrelationSignalStatus
                .PARTIAL_MATCH
            )
            contribution = self.TEMPORAL_WEIGHT * 0.20
            explanation = (
                "The candidate observation occurs after the declared search "
                "time and requires human review."
            )

        else:
            difference_days = (
                difference_seconds
                / 86_400
            )

            if difference_days <= 7:
                status = CorrelationSignalStatus.MATCH
                contribution = self.TEMPORAL_WEIGHT
                explanation = (
                    "The candidate observation is within seven days of the "
                    "search."
                )

            elif difference_days <= 30:
                status = (
                    CorrelationSignalStatus
                    .PARTIAL_MATCH
                )
                contribution = self.TEMPORAL_WEIGHT * 0.75
                explanation = (
                    "The candidate observation is within thirty days of the "
                    "search."
                )

            elif difference_days <= 180:
                status = (
                    CorrelationSignalStatus
                    .PARTIAL_MATCH
                )
                contribution = self.TEMPORAL_WEIGHT * 0.45
                explanation = (
                    "The candidate observation is older, but remains "
                    "temporally plausible."
                )

            else:
                status = (
                    CorrelationSignalStatus
                    .PARTIAL_MATCH
                )
                contribution = self.TEMPORAL_WEIGHT * 0.20
                explanation = (
                    "The candidate observation is substantially older and "
                    "requires careful human interpretation."
                )

        signals.append(
            CorrelationSignal(
                field="observation.temporal_distance",
                status=status,
                contribution=round(
                    contribution,
                    2,
                ),
                explanation=explanation,
                query_value=self._datetime_text(
                    searched_at
                ),
                record_value=self._datetime_text(
                    observed_at
                ),
            )
        )

    def _append_text_signal(
        self,
        signals: list[CorrelationSignal],
        field: str,
        query_value: str | None,
        record_value: str | None,
        weight: float,
        description: str,
        containment_floor: float,
    ) -> None:
        """
        Compara texto libre y genera una señal explicable.
        """
        if query_value is None:
            return

        if record_value is None:
            signals.append(
                CorrelationSignal(
                    field=field,
                    status=(
                        CorrelationSignalStatus
                        .NOT_AVAILABLE
                    ),
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
            containment_floor=containment_floor,
        )

        status = self._similarity_status(
            similarity
        )

        contribution = (
            self._contribution_for_similarity(
                similarity=similarity,
                status=status,
                weight=weight,
            )
        )

        explanation = (
            self._text_signal_explanation(
                description=description,
                similarity=similarity,
                status=status,
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

    def _append_age_signal(
        self,
        signals: list[CorrelationSignal],
        query_age: int | None,
        record_age: int | None,
    ) -> None:
        """
        Compara edades humanas con la regla principal de ±3 años.
        """
        if query_age is None:
            return

        if record_age is None:
            signals.append(
                CorrelationSignal(
                    field="subject.estimated_age",
                    status=(
                        CorrelationSignalStatus
                        .NOT_AVAILABLE
                    ),
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

        age_difference = abs(
            query_age - record_age
        )

        if age_difference == 0:
            status = CorrelationSignalStatus.MATCH
            contribution = self.ESTIMATED_AGE_WEIGHT
            explanation = (
                "The estimated ages are equal."
            )

        elif age_difference <= 3:
            status = CorrelationSignalStatus.MATCH
            contribution = (
                self.ESTIMATED_AGE_WEIGHT
                * (
                    1.0
                    - age_difference * 0.08
                )
            )
            explanation = (
                "The estimated ages are strongly compatible with a "
                f"difference of {age_difference} year"
                f"{'' if age_difference == 1 else 's'}."
            )

        elif age_difference <= 5:
            status = (
                CorrelationSignalStatus
                .PARTIAL_MATCH
            )
            contribution = (
                self.ESTIMATED_AGE_WEIGHT
                * 0.50
            )
            explanation = (
                "The estimated ages are partially compatible with a "
                f"difference of {age_difference} years."
            )

        else:
            status = CorrelationSignalStatus.CONFLICT
            contribution = 0.0
            explanation = (
                "The estimated ages conflict with a difference of "
                f"{age_difference} years."
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
        query_value: str | None,
        record_value: str | None,
        weight: float,
        description: str,
    ) -> None:
        """
        Compara un valor canónico exacto.
        """
        if query_value is None:
            return

        if record_value is None:
            signals.append(
                CorrelationSignal(
                    field=field,
                    status=(
                        CorrelationSignalStatus
                        .NOT_AVAILABLE
                    ),
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

    @classmethod
    def _calculate_normalized_score(
        cls,
        signals: list[CorrelationSignal],
    ) -> float:
        """
        Calcula la compatibilidad usando únicamente datos declarados.

        Los datos ausentes en el registro conservan su peso disponible y por
        eso reducen el porcentaje, sin convertirse en contradicciones.
        """
        if not signals:
            return 0.0

        weights = cls._signal_weights()

        total_available_weight = 0.0
        total_contribution = 0.0

        for signal in signals:
            weight = weights.get(
                signal.field
            )

            if weight is None:
                continue

            total_available_weight += weight
            total_contribution += (
                signal.contribution
            )

        if total_available_weight == 0.0:
            return 0.0

        normalized_score = (
            total_contribution
            / total_available_weight
            * 100.0
        )

        return round(
            min(
                max(
                    normalized_score,
                    0.0,
                ),
                100.0,
            ),
            2,
        )

    @classmethod
    def _calculate_evidence_strength(
        cls,
        signals: list[CorrelationSignal],
    ) -> float:
        """
        Calcula la amplitud y disponibilidad de la evidencia.

        La fuerza de evidencia no es el porcentaje de compatibilidad.

        Depende de:

        - cuántos grupos independientes fueron comparados;
        - cuántos datos estaban disponibles;
        - si existe evidencia descriptiva y espacial suficiente.
        """
        if not signals:
            return 0.0

        weights = cls._signal_weights()

        total_requested_weight = 0.0
        available_weight = 0.0

        available_groups: set[str] = set()
        requested_groups: set[str] = set()

        for signal in signals:
            weight = weights.get(
                signal.field
            )

            if weight is None:
                continue

            group = cls._evidence_group(
                signal.field
            )

            requested_groups.add(group)
            total_requested_weight += weight

            if (
                signal.status
                != CorrelationSignalStatus
                .NOT_AVAILABLE
            ):
                available_weight += weight
                available_groups.add(group)

        if total_requested_weight == 0.0:
            return 0.0

        availability_percentage = (
            available_weight
            / total_requested_weight
            * 100.0
        )

        group_coverage = (
            len(available_groups)
            / len(requested_groups)
            * 100.0
            if requested_groups
            else 0.0
        )

        strength = (
            availability_percentage * 0.65
            + group_coverage * 0.35
        )

        # Evita declarar evidencia muy alta cuando solo existe un grupo,
        # aunque ese único dato sea exacto.
        if len(available_groups) == 1:
            strength = min(
                strength,
                35.0,
            )

        elif len(available_groups) == 2:
            strength = min(
                strength,
                60.0,
            )

        return round(
            min(
                max(
                    strength,
                    0.0,
                ),
                100.0,
            ),
            2,
        )

    @classmethod
    def _signal_weights(
        cls,
    ) -> dict[str, float]:
        return {
            "subject.reported_label": (
                cls.REPORTED_LABEL_WEIGHT
            ),
            "subject.estimated_age": (
                cls.ESTIMATED_AGE_WEIGHT
            ),
            "subject.recognition_features": (
                cls.RECOGNITION_FEATURES_WEIGHT
            ),
            "subject.species": (
                cls.SPECIES_WEIGHT
            ),
            "subject.size": (
                cls.ANIMAL_SIZE_WEIGHT
            ),
            "subject.breed": (
                cls.BREED_WEIGHT
            ),
            (
                "observation.declared_location."
                "country_code"
            ): cls.COUNTRY_WEIGHT,
            (
                "observation.declared_location."
                "admin_level_1"
            ): cls.ADMIN_LEVEL_1_WEIGHT,
            (
                "observation.declared_location."
                "admin_level_2"
            ): cls.ADMIN_LEVEL_2_WEIGHT,
            (
                "observation.declared_location."
                "locality"
            ): cls.LOCALITY_WEIGHT,
            (
                "observation.declared_location."
                "district"
            ): cls.DISTRICT_WEIGHT,
            "observation.reported_location": (
                cls.ADMIN_LEVEL_1_WEIGHT
                + cls.LOCALITY_WEIGHT
            ),
            "observation.temporal_distance": (
                cls.TEMPORAL_WEIGHT
            ),
        }

    @staticmethod
    def _evidence_group(
        field: str,
    ) -> str:
        if field.startswith(
            "observation.declared_location"
        ) or field == (
            "observation.reported_location"
        ):
            return "space"

        if field == (
            "observation.temporal_distance"
        ):
            return "time"

        if field == (
            "subject.estimated_age"
        ):
            return "age"

        if field in {
            "subject.reported_label",
            "subject.recognition_features",
        }:
            return "description"

        return "animal_description"

    @staticmethod
    def _similarity_status(
        similarity: float,
    ) -> CorrelationSignalStatus:
        if (
            similarity
            >= CorrelationService
            .STRONG_TEXT_SIMILARITY
        ):
            return (
                CorrelationSignalStatus
                .MATCH
            )

        if (
            similarity
            >= CorrelationService
            .PARTIAL_TEXT_SIMILARITY
        ):
            return (
                CorrelationSignalStatus
                .PARTIAL_MATCH
            )

        return (
            CorrelationSignalStatus
            .CONFLICT
        )

    @staticmethod
    def _contribution_for_similarity(
        similarity: float,
        status: CorrelationSignalStatus,
        weight: float,
    ) -> float:
        if (
            status
            == CorrelationSignalStatus
            .CONFLICT
        ):
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

        if (
            status
            == CorrelationSignalStatus.MATCH
        ):
            return (
                f"The {description} evidence is strongly compatible "
                f"({similarity_percentage}% textual similarity)."
            )

        if (
            status
            == CorrelationSignalStatus
            .PARTIAL_MATCH
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
    def _text_similarity(
        cls,
        first_value: str,
        second_value: str,
        containment_floor: float,
    ) -> float:
        """
        Calcula similitud determinística y tolerante.

        Premia:

        - igualdad exacta;
        - nombre parcial contenido en nombre completo;
        - palabras compartidas;
        - pequeños errores de escritura;
        - diferencias de acentuación y puntuación.
        """
        first_normalized = (
            cls._normalize_text(
                first_value
            )
        )
        second_normalized = (
            cls._normalize_text(
                second_value
            )
        )

        if (
            not first_normalized
            or not second_normalized
        ):
            return 0.0

        if (
            first_normalized
            == second_normalized
        ):
            return 1.0

        containment_score = 0.0

        if (
            first_normalized
            in second_normalized
            or second_normalized
            in first_normalized
        ):
            shortest_length = min(
                len(first_normalized),
                len(second_normalized),
            )
            longest_length = max(
                len(first_normalized),
                len(second_normalized),
            )

            length_ratio = (
                shortest_length
                / longest_length
                if longest_length
                else 0.0
            )

            containment_score = max(
                containment_floor,
                length_ratio,
            )

        sequence_score = (
            SequenceMatcher(
                None,
                first_normalized,
                second_normalized,
            ).ratio()
        )

        first_tokens = set(
            first_normalized.split()
        )
        second_tokens = set(
            second_normalized.split()
        )

        token_jaccard = 0.0
        query_token_coverage = 0.0

        if (
            first_tokens
            and second_tokens
        ):
            intersection = (
                first_tokens
                & second_tokens
            )
            union = (
                first_tokens
                | second_tokens
            )

            token_jaccard = (
                len(intersection)
                / len(union)
            )

            # Es importante cuando la consulta contiene solo una parte de la
            # descripción: "señora" dentro de una descripción más extensa.
            query_token_coverage = (
                len(intersection)
                / min(
                    len(first_tokens),
                    len(second_tokens),
                )
            )

        return round(
            max(
                containment_score,
                sequence_score,
                token_jaccard,
                query_token_coverage
                * 0.80,
            ),
            4,
        )

    @staticmethod
    def _format_location(
        location: object,
    ) -> str:
        values = [
            getattr(
                location,
                "district",
                None,
            ),
            getattr(
                location,
                "locality",
                None,
            ),
            getattr(
                location,
                "admin_level_2",
                None,
            ),
            getattr(
                location,
                "admin_level_1",
                None,
            ),
            getattr(
                location,
                "country_code",
                None,
            ),
        ]

        return ", ".join(
            str(value).strip()
            for value in values
            if value is not None
            and str(value).strip()
        )

    @staticmethod
    def _as_datetime(
        value: object,
    ) -> datetime | None:
        if isinstance(
            value,
            datetime,
        ):
            return value

        if isinstance(
            value,
            str,
        ):
            try:
                return datetime.fromisoformat(
                    value.replace(
                        "Z",
                        "+00:00",
                    )
                )
            except ValueError:
                return None

        return None

    @staticmethod
    def _datetime_text(
        value: object,
    ) -> str:
        if isinstance(
            value,
            datetime,
        ):
            return value.isoformat()

        return str(value)

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
