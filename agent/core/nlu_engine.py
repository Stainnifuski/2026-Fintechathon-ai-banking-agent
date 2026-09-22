"""兼容导入。实际入口在 backend.app.services.ai，复用同一校验与配置。"""
from backend.app.services.ai.nlu import Extraction, ModelAdapter, NLUEngine, amount_fen

__all__ = ["Extraction", "ModelAdapter", "NLUEngine", "amount_fen"]
