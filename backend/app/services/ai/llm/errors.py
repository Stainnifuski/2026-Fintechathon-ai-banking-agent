"""模型错误只暴露稳定错误码，不携带响应正文、请求或凭据。"""


class LLMError(ValueError):
    def __init__(self, code: str, *, finish_reason: str | None = None) -> None:
        self.code = code
        self.finish_reason = finish_reason
        super().__init__(code)
