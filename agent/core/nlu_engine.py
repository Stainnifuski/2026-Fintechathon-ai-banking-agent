"""意图理解引擎：意图分类 + 实体提取 + 槽位填充。

本模块负责将用户的自然语言输入转化为结构化的意图和实体信息，
是 Agent 理解用户需求的第一步。通过 LLM Function Calling 实现
意图分类、实体提取和槽位填充。
"""

from pydantic import BaseModel, Field


# MVP 阶段支持的意图类型
INTENT_TYPES: list[str] = [
    "transfer",           # 转账
    "query_balance",      # 查余额
    "query_transactions", # 查交易记录
    "confirmation",       # 确认（"确认"、"好的"）
    "cancellation",       # 取消（"取消"、"算了"）
    "correction",         # 纠错（"不对，是转给李四"）
    "unknown",            # 无法识别
]


class TransferSlots(BaseModel):
    """转账场景的槽位数据模型。

    用于收集转账所需的关键信息，未收集到的槽位值为 None。
    """

    recipient: str | None = Field(default=None, description="收款人姓名或手机号")
    amount: float | None = Field(default=None, description="转账金额（元）")
    note: str | None = Field(default=None, description="转账备注")
    scheduled_time: str | None = Field(default=None, description="定时转账时间，ISO 格式")


class IntentResult(BaseModel):
    """意图识别结果数据模型。"""

    intent: str = Field(description="识别到的意图类型，取值见 INTENT_TYPES")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="置信度 0~1")
    slots: dict = Field(default_factory=dict, description="提取到的槽位键值对")
    raw_entities: list = Field(default_factory=list, description="原始实体列表")


class NLUEngine:
    """意图理解引擎。

    通过 LLM Function Calling 对用户输入进行意图分类、实体提取与槽位填充。
    """

    def __init__(self, llm_client: object) -> None:
        """初始化意图理解引擎。

        Args:
            llm_client: LLM 客户端实例，需兼容 OpenAI API 格式。
        """
        self.llm_client = llm_client

    async def classify_intent(
        self,
        user_input: str,
        context: dict | None = None,
    ) -> IntentResult:
        """通过 LLM Function Calling 识别用户意图。

        Args:
            user_input: 用户输入的自然语言文本。
            context: 可选的对话上下文信息，用于多轮理解。

        Returns:
            IntentResult: 包含意图类型、置信度、槽位和原始实体的识别结果。
        """
        # TODO: 实现具体逻辑 — 构造 Function Calling 请求并解析 LLM 返回
        return IntentResult(
            intent="unknown",
            confidence=0.0,
            slots={},
            raw_entities=[],
        )

    async def extract_transfer_slots(
        self,
        user_input: str,
        context: dict | None = None,
    ) -> TransferSlots:
        """从用户输入中提取转账相关槽位信息。

        Args:
            user_input: 用户输入的自然语言文本。
            context: 可选的对话上下文信息。

        Returns:
            TransferSlots: 提取到的转账槽位（未识别到的字段为 None）。
        """
        # TODO: 实现具体逻辑 — 调用 LLM 提取收款人、金额、备注等
        return TransferSlots()

    async def resolve_ambiguity(
        self,
        user_input: str,
        missing_slots: list[str],
    ) -> str:
        """针对缺失槽位生成自然语言追问语句。

        Args:
            user_input: 用户原始输入。
            missing_slots: 当前缺失的槽位名称列表。

        Returns:
            str: 生成的追问语句，引导用户补充缺失信息。
        """
        # TODO: 实现具体逻辑 — 根据缺失槽位生成友好的追问
        slot_names_cn: dict[str, str] = {
            "recipient": "收款人",
            "amount": "转账金额",
            "note": "转账备注",
        }
        missing_cn = [slot_names_cn.get(s, s) for s in missing_slots]
        return f"请补充以下信息：{'、'.join(missing_cn)}"
