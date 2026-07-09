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
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.infrastructure.logging import configure_logging, get_logger
from app.routers.admin_router import router as admin_router
from app.routers.auth_router import router as auth_router
from app.routers.dashboard_router import router as dashboard_router
from app.routers.vault_router import router as vault_router
from app.routers.webhook_router import get_sessions, router as webhook_router
from app.services.ticket_service import sweep_expired_unnotified
from app.dependencies import get_tickets, get_machines

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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    log.info("turbofix.startup", store=config.TICKET_STORE, doc_store=config.DOCUMENT_STORE)
    sweep_task = asyncio.create_task(_sweep_loop())
    try:
        yield
    finally:
        sweep_task.cancel()
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
app.include_router(admin_router)


@app.get("/health", tags=["ops"])
def health():
    """Health check for Railway / load balancers."""
    return {"status": "ok", "store": config.TICKET_STORE}
