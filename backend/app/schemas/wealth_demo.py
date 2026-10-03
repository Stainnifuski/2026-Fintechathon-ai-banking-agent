"""可信测试工具入参；主体只由认证边界补入，金额不接受浮点。"""
from typing import Literal
from pydantic import Field
from .wealth import StrictRequest


class RunVersion(StrictRequest):
    run_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1, strict=True)


class DemoReset(RunVersion):
    request_id: str = Field(min_length=8, max_length=64)


class DemoAdjust(DemoReset):
    action: Literal['credit', 'debit']
    amount: str = Field(pattern=r'^(?:0|[1-9]\d{0,8})(?:\.\d{1,2})?$')


class DemoDeletePreview(StrictRequest):
    runs: list[RunVersion] = Field(min_length=1, max_length=100)


class DemoDelete(DemoDeletePreview):
    request_id: str = Field(min_length=8, max_length=64)
    confirmed: bool = Field(strict=True)
    preview_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
