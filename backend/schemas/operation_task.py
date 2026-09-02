"""Öffentlicher, secret-freier Statusvertrag für Backend-Aufgaben."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OperationTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_type: str
    actor_user_id: int | None
    origin: str
    correlation_id: str
    status: str
    phase: str
    server_id: int | None
    retry_of_id: str | None
    attempt: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime
