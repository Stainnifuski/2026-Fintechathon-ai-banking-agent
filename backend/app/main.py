"""FastAPI 应用入口"""

import asyncio
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database.connection import AsyncSessionLocal, close_db, init_db
from .database.seed import seed_data
from .integrations.bank_api import get_bank_api
from .middleware.audit import AuditMiddleware
from .middleware.errors import install_error_handlers
from .routers.accounts import router as accounts_router
from .routers.auth import router as auth_router
from .routers.bank_operations import router as bank_operations_router
from .routers.cards import router as cards_router
from .routers.operations import router as operations_router
from .routers.payment_requests import router as payment_requests_router
from .routers.transactions import router as transactions_router
from .routers.tasks import router as tasks_router
from .routers.subscriptions import router as subscriptions_router
from .routers.users import router as users_router
from .routers.bills import router as bills_router
from .routers.wealth_bank import router as wealth_bank_router
from .routers.wealth import router as wealth_router
from .routers.system import router as system_router
from .routers.wealth_tasks import router as wealth_tasks_router
from .routers.flower_shop import router as flower_shop_router
from .routers.cake_shop import router as cake_shop_router
from .routers.calendar import router as calendar_router
from .services.orchestration.scheduler import scheduled_transfer_loop
from .services.bank.wealth_worker import settlement_loop
from .services.orchestration.wealth_task_worker import loop as wealth_task_loop
from .services.ai.llm import get_mode, get_settings, llm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期管理：启动时初始化数据库，关闭时释放连接。"""
    workers: list[asyncio.Task] = []
    try:
        mode = get_mode()
        if mode == "model":
            settings = get_settings()
            logger.info("统一 LLM 服务：mode=%s provider=%s model=%s", mode, settings.provider, settings.model)
        else:
            logger.info("统一 LLM 服务：mode=offline")
        logger.info("正在初始化数据库...")
        await init_db()
        logger.info("正在加载种子数据...")
        await seed_data()
        workers.append(asyncio.create_task(
            scheduled_transfer_loop(AsyncSessionLocal, get_bank_api()), name="scheduled-transfer-loop"))
        workers.append(asyncio.create_task(settlement_loop(AsyncSessionLocal), name="wealth-settlement-loop"))
        workers.append(asyncio.create_task(wealth_task_loop(AsyncSessionLocal), name="wealth-dag-loop"))
        logger.info("AI Banking Agent API 启动完成 🚀")
        yield
    finally:
        for task in workers:
            task.cancel()
        outcomes = await asyncio.gather(*workers, return_exceptions=True)
        for task, outcome in zip(workers, outcomes):
            if isinstance(outcome, Exception):
                logger.error("后台任务 %s 异常退出：%s", task.get_name(), type(outcome).__name__)
        try:
            await llm.aclose()
        finally:
            logger.info("正在关闭数据库连接...")
            await close_db()
        logger.info("服务已关闭")


app = FastAPI(
    title="AI Banking Agent API",
    version="0.1.0",
    description="AI 银行智能体后端服务",
    lifespan=lifespan,
)
app.include_router(wealth_tasks_router)

# ──────────────────── 中间件 ────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(AuditMiddleware)
install_error_handlers(app)

# ──────────────────── 路由 ────────────────────

app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(transactions_router)
app.include_router(tasks_router)
app.include_router(operations_router)
app.include_router(users_router)
app.include_router(bank_operations_router)
app.include_router(payment_requests_router)
app.include_router(subscriptions_router)
app.include_router(cards_router)
app.include_router(bills_router)
app.include_router(wealth_bank_router)
app.include_router(wealth_router)
app.include_router(system_router)
app.include_router(flower_shop_router)
app.include_router(cake_shop_router)
app.include_router(calendar_router)


# ──────────────────── 健康检查 ────────────────────


@app.get("/health", tags=["system"])
async def health_check() -> dict:
    """健康检查接口。"""
    return {"status": "ok", "service": "AI Banking Agent API", "version": "0.1.0"}
