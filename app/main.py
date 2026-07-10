"""TurboFix backend entry point — thin application bootstrap.

This module's only jobs:
  1. Configure logging.
  2. Start the session-sweep background task (lifespan).
  3. Mount all routers.
  4. Add middleware.
  5. Expose the /health check.

All business logic lives in app/services/.
All data access lives in app/repositories/.
All HTTP handling lives in app/routers/.
"""

import asyncio
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.infrastructure.logging import configure_logging, get_logger
from app.routers.admin_router import router as admin_router
from app.routers.report_router import router as report_router
from app.routers.auth_router import router as auth_router
from app.routers.dashboard_router import router as dashboard_router
from app.routers.kpi_router import router as kpi_router
from app.routers.vault_router import router as vault_router
from app.routers.webhook_router import get_sessions, router as webhook_router
from app.services.ticket_service import sweep_expired_unnotified
from app.services.escalation_service import escalation_loop
from app.dependencies import get_events, get_tickets, get_machines, get_users

configure_logging()
log = get_logger("turbofix.main")


async def _sweep_loop() -> None:
    """Background loop: fire fallback fan-outs for sessions that expired without notification."""
    while True:
        await asyncio.sleep(config.SESSION_SWEEP_INTERVAL_SECONDS)
        try:
            sessions = get_sessions()
            tickets = get_tickets()
            machines = get_machines()
            await sweep_expired_unnotified(sessions, tickets, machines)
        except Exception as exc:
            log.error("sweep.error", error=str(exc))


KEEP_ALIVE_URL = os.getenv("KEEP_ALIVE_URL", "")
KEEP_ALIVE_INTERVAL = int(os.getenv("KEEP_ALIVE_INTERVAL", "840"))  # 14 minutes


async def _keep_alive_loop() -> None:
    """Ping our own public URL every 14 min to prevent Render free-tier from sleeping."""
    if not KEEP_ALIVE_URL:
        return
    log.info("keepalive.started", url=KEEP_ALIVE_URL, interval=KEEP_ALIVE_INTERVAL)
    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.sleep(KEEP_ALIVE_INTERVAL)
            try:
                resp = await client.get(f"{KEEP_ALIVE_URL}/health", timeout=10)
                log.info("keepalive.ping", status=resp.status_code)
            except Exception as exc:
                log.warning("keepalive.failed", error=str(exc))


@asynccontextmanager
async def _lifespan(app: FastAPI):
    log.info("turbofix.startup", store=config.TICKET_STORE, doc_store=config.DOCUMENT_STORE)
    sweep_task = asyncio.create_task(_sweep_loop())
    escalation_task = asyncio.create_task(escalation_loop(get_users))
    keepalive_task = asyncio.create_task(_keep_alive_loop())
    try:
        yield
    finally:
        sweep_task.cancel()
        escalation_task.cancel()
        keepalive_task.cancel()
        log.info("turbofix.shutdown")


app = FastAPI(
    title="TurboFix API — SOLID Architecture",
    description="WhatsApp-native maintenance ticketing for MSMEs.",
    version="2.0.0",
    lifespan=_lifespan,
)

# CORS — restrict to the deployed frontend origin in production.
# Set VAULT_CORS_ORIGINS=https://your-site.github.io in Railway env vars.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.VAULT_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all routers — the order matters for OpenAPI grouping.
app.include_router(webhook_router)
app.include_router(auth_router)
app.include_router(vault_router)
app.include_router(dashboard_router)
app.include_router(kpi_router)
app.include_router(admin_router)
app.include_router(report_router)


@app.get("/health", tags=["ops"])
def health():
    """Health check for Railway / load balancers."""
    return {
        "status": "ok",
        "store": config.TICKET_STORE,
        "doc_store": config.DOCUMENT_STORE,
        "drive_folder_set": bool(config.GOOGLE_DRIVE_FOLDER_ID),
        "sa_file_set": bool(config.GOOGLE_SERVICE_ACCOUNT_FILE),
    }
