from datetime import datetime
from typing import Optional, Dict, Any
from uuid import UUID
from pydantic import BaseModel, Field


class EventClassification(BaseModel):
    category: str = Field(..., example="Human Observation")
    type: str = Field(..., example="Missing Person Observation")
    code: Optional[str] = Field(None, example="HO-001")


class Observation(BaseModel):
    reported_name: Optional[str] = None
    estimated_age: Optional[int] = None
    reported_location: Optional[str] = None
    humanitarian_status: Optional[str] = None
    description: Optional[str] = None


class Source(BaseModel):
    source_type: str = Field(..., example="Volunteer")
    organization_name: Optional[str] = None


class HumanitarianRecordCreate(BaseModel):
    event_classification: EventClassification
    observation: Observation
    source: Source
    metadata: Optional[Dict[str, Any]] = None


class HumanitarianRecord(BaseModel):
    record_uuid: UUID
    protocol_version: str = "HCP-0.1"
    created_at: datetime
    event_classification: EventClassification
    observation: Observation
    source: Source
    metadata: Optional[Dict[str, Any]] = None
