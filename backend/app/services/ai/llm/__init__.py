"""业务模块统一调用此入口，不直接依赖外部模型协议。"""

from .client import LLMResult, LLMService, llm
from .config import LLMSettings, get_mode, get_settings, get_timeout
from .errors import LLMError

__all__ = ["LLMError", "LLMResult", "LLMService", "LLMSettings", "get_mode", "get_settings", "get_timeout", "llm"]
