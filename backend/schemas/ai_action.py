"""Secret-minimierte API-Vertraege fuer AI-Aktionsvorschlaege."""

from datetime import datetime

from pydantic import BaseModel, Field, SecretStr


class AiActionProposalResponse(BaseModel):
    id: str
    conversation_id: str
    server_id: int
    tool_name: str
    preview: dict
    expected_revision: str | None
    requires_confirmation: bool
    status: str
    task_id: str | None
    error_code: str | None
    created_at: datetime


class AiActionConfirmationResponse(BaseModel):
    proposal_id: str
    confirmation_token: str
    expires_at: datetime


class AiActionExecuteRequest(BaseModel):
    confirmation_token: SecretStr = Field(min_length=32, max_length=256)


class AiActionExecuteResponse(BaseModel):
    proposal: AiActionProposalResponse
    result: dict
