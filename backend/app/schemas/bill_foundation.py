from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing import Annotated


class AnnotationUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    tags: list[Annotated[str, StringConstraints(min_length=1, max_length=24)]] = Field(max_length=10)
    expected_version: int = Field(ge=0)


class MerchantMerge(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source_merchant_id: str = Field(min_length=1, max_length=40)


class MerchantAliasCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=64)


class RecurringUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool
    expected_version: int = Field(ge=1)
