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
    external_dependencies: list[str] = Field(default_factory=list,max_length=4)

    @model_validator(mode='after')
    def valid(self):
        if self.schema_version!='wealth-subtask-v1':raise ValueError('unknown subtask schema')
        if self.parsed_intent and not self.parsed_intent.semantic:raise ValueError('typed input requires semantic frame')
        if self.external_dependencies and (not self.parent_task_id or self.session_id):
            raise ValueError('deferred parent dependencies require parent id and a fresh child session')
        if len(set(self.external_dependencies))!=len(self.external_dependencies) or any(not 1<=len(d)<=64 for d in self.external_dependencies):
            raise ValueError('dependency identifiers invalid')
        return self


class SubtaskPatch(StrictRequest):
    request_id: str = Field(min_length=8,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    expected_version: int = Field(ge=1)
    raw_text: str = Field(min_length=1,max_length=500)
    parsed_intent: Intent | None = None


class ExternalCompletion(StrictRequest):
    request_id: str = Field(min_length=8,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    expected_version: int = Field(ge=1,strict=True)
    parent_task_id: str = Field(min_length=1,max_length=64)
    node_id: str = Field(min_length=1,max_length=128)
    event_reference: str = Field(min_length=1,max_length=64)
