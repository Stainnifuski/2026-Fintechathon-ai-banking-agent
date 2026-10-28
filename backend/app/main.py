"""FastAPI 应用入口"""

import asyncio
import logging
import os
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
from .routers.nova import router as nova_router
from .routers.bank_conversations import router as bank_conversations_router
from .routers.wealth_tasks import router as wealth_tasks_router
from .routers.flower_shop import router as flower_shop_router
from .routers.cake_shop import router as cake_shop_router
from .routers.calendar import router as calendar_router
from .routers.audit import router as audit_router
from .routers.voice import router as voice_router
from .routers.scheduled_tasks import router as scheduled_tasks_router
from .services.orchestration.scheduler import scheduled_transfer_loop, subscription_charge_loop
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
        from .security.release_integrity import verify_skills
        verify_skills()
        from .database.connection import DATABASE_URL
        from .integrations.local_runtime import configure_local_test_limits
        app.state.local_test_mode = configure_local_test_limits(DATABASE_URL)
        mode = get_mode()
        if mode == "model":
            settings = get_settings()
            logger.info("统一 LLM 服务：mode=%s provider=%s model=%s", mode, settings.provider, settings.model)
        else:
            logger.info("统一 LLM 服务：mode=offline")
        logger.info("正在初始化数据库...")
        if os.getenv('WEALTH_CATALOG_AUTO_UPGRADE','false').lower()=='true':
            from .services.bank.wealth_compact_upgrade import prepare_deployment
            await prepare_deployment(DATABASE_URL)
        await init_db()
        seed_mode = os.getenv('BANK_SEED_DEMO_DATA', 'true').lower()
        if seed_mode not in {'true', 'false'}:
            raise ValueError('BANK_SEED_DEMO_DATA must be true or false')
        if seed_mode == 'true':
            logger.info("正在加载种子数据...")
            await seed_data()
        else:
            logger.info('保留现有账本；不注入通用演示种子')
        if os.getenv('WEALTH_CATALOG_AUTO_UPGRADE','false').lower()=='true':
            from .services.bank.wealth_compact_upgrade import upgrade as upgrade_catalog
            async with AsyncSessionLocal() as upgrade_session:
                result=await upgrade_catalog(upgrade_session)
                await upgrade_session.commit()
                logger.info('理财资料升级：%s',result)
        from .services.bank.wealth_retire_pending import retire as retire_deferred
        async with AsyncSessionLocal() as retirement_session:
            retired=await retire_deferred(retirement_session)
            await retirement_session.commit()
            logger.info('Legacy deferred execution retired: %s',retired)
        from .services.bank.bill_foundation import refresh_all
        async with AsyncSessionLocal() as bill_session:
            await refresh_all(bill_session)
            await bill_session.commit()
        # Material initialization is separate from request latency. Missing
        # offline material keeps the ordinary app alive, but A reads fail 503;
        # there is never a fixture fallback.
        try:
            initialized=await app.state.wealth_portfolio_source.preload()
            logger.info('Portfolio immutable index ready: %s',initialized)
        except Exception as error:
            logger.warning('Portfolio material unavailable: %s',type(error).__name__)
        workers.append(asyncio.create_task(
            scheduled_transfer_loop(AsyncSessionLocal, get_bank_api()), name="scheduled-transfer-loop"))
        workers.append(asyncio.create_task(
            subscription_charge_loop(AsyncSessionLocal), name="subscription-charge-loop"))
        workers.append(asyncio.create_task(wealth_task_loop(AsyncSessionLocal), name="wealth-dag-loop"))
        from .services.audit.writer import recovery_loop
        workers.append(asyncio.create_task(recovery_loop(AsyncSessionLocal), name="audit-recovery-loop"))
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
app.include_router(scheduled_tasks_router)
from .routers.bill_foundations import router as bill_foundations_router
app.include_router(bill_foundations_router)
from .routers.bill_features import router as bill_features_router
app.include_router(bill_features_router)
from .routers.wealth_portfolio import router as portfolio_router
from .services.orchestration.wealth_portfolio_joint import install as install_portfolio
from pathlib import Path
install_portfolio(app,Path(__file__).resolve().parents[2])
app.include_router(portfolio_router)

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
app.include_router(nova_router)
from .routers.nova_bills import router as nova_bills_router
app.include_router(nova_bills_router)
app.include_router(bank_conversations_router)
app.include_router(flower_shop_router)
app.include_router(cake_shop_router)
app.include_router(calendar_router)
app.include_router(audit_router)
app.include_router(voice_router)


# ──────────────────── 健康检查 ────────────────────


@app.get("/health", tags=["system"])
async def health_check() -> dict:
    """健康检查接口。"""
    from .services.audit.writer import health as audit_health
    return {"status": "ok", "service": "AI Banking Agent API", "version": "0.1.0", "audit": audit_health(),
        "portfolio_ready":bool(getattr(app.state.wealth_portfolio_source,'ready',False)),
        "source_revision":os.getenv('SOURCE_REVISION','source-workspace'),
        "local_test_mode":bool(getattr(app.state,'local_test_mode',False))}


from .services.audit.routes import install_routes as install_audit_routes
install_audit_routes(app)
