"""所有层级共用的规范化订单摘要，不包含验证凭据。"""

import hashlib
import json

from ..schemas.wealth import InvestmentCommand


def command_digest(command: InvestmentCommand) -> str:
    """操作号独立于业务参数；摘要包含计划、规则、披露版本。"""
    payload = command.model_dump(exclude={"operation_id"})
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()
