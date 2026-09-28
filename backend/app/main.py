"""FastAPI 应用入口"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
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
from .routers.operations import router as operations_router
from .routers.payment_requests import router as payment_requests_router
from .routers.transactions import router as transactions_router
from .routers.tasks import router as tasks_router
from .routers.subscriptions import router as subscriptions_router
from .routers.users import router as users_router
from .services.orchestration.scheduler import scheduled_transfer_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期管理：启动时初始化数据库，关闭时释放连接。"""
    logger.info("正在初始化数据库...")
    await init_db()
    logger.info("正在加载种子数据...")
    await seed_data()
    logger.info("AI Banking Agent API 启动完成 🚀")
    scheduler_task = asyncio.create_task(
        scheduled_transfer_loop(AsyncSessionLocal, get_bank_api()),
        name="scheduled-transfer-loop",
    )
    try:
        yield
    finally:
        scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler_task
    logger.info("正在关闭数据库连接...")
    await close_db()
    logger.info("服务已关闭")


app = FastAPI(
    title="AI Banking Agent API",
    version="0.1.0",
    description="AI 银行智能体后端服务",
    lifespan=lifespan,
)

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


# ──────────────────── 健康检查 ────────────────────


@app.get("/health", tags=["system"])
async def health_check() -> dict:
    """健康检查接口。"""
    return {"status": "ok", "service": "AI Banking Agent API", "version": "0.1.0"}
