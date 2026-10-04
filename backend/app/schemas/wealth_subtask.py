from pydantic import Field,model_validator
from .wealth import StrictRequest
from ..services.ai.wealth_model import Intent


class SubtaskCreate(StrictRequest):
    schema_version: str = 'wealth-subtask-v1'
    request_id: str = Field(min_length=8,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    raw_text: str = Field(min_length=1,max_length=500)
    parsed_intent: Intent | None = None
    context_id: str = Field(min_length=1,max_length=64)
    parent_task_id: str | None = Field(default=None,max_length=64)
    session_id: str | None = Field(default=None,max_length=64)
    version: int | None = Field(default=None,ge=1)

    @model_validator(mode='after')
    def valid(self):
        if self.schema_version!='wealth-subtask-v1':raise ValueError('unknown subtask schema')
        if self.parsed_intent and not self.parsed_intent.semantic:raise ValueError('typed input requires semantic frame')
        return self


class SubtaskPatch(StrictRequest):
    request_id: str = Field(min_length=8,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    expected_version: int = Field(ge=1)
    raw_text: str = Field(min_length=1,max_length=500)
    parsed_intent: Intent | None = None
