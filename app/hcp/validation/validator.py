from app.schemas.humanitarian_record import HumanitarianRecordCreate


def validate_humanitarian_record_payload(payload: HumanitarianRecordCreate) -> None:
    if not payload.event_classification.category:
        raise ValueError("Event classification category is required")

    if not payload.event_classification.type:
        raise ValueError("Event classification type is required")

    if not payload.source.source_type:
        raise ValueError("Source type is required")

    observation = payload.observation

    if not any([
        observation.reported_name,
        observation.reported_location,
        observation.humanitarian_status,
        observation.description,
    ]):
        raise ValueError("At least one observation field must be provided")
