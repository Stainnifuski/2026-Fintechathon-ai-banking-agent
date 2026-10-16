"""B-owned prepare request. PortfolioPlan/Selection remain owned by A."""
from pydantic import Field
from .wealth import StrictRequest


class PortfolioPrepare(StrictRequest):
    expected_version: int = Field(ge=1, strict=True)
    snapshot_id: str = Field(min_length=1, max_length=128)
    payment_account_id: str | None = Field(default=None,min_length=1,max_length=64)
