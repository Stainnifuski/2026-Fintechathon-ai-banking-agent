"""模型错误只暴露稳定错误码，不携带响应正文、请求或凭据。"""


class LLMError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
