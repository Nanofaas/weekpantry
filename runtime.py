"""NanoFaaS HTTP function contract. No web-facing routes or local durable state."""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from domain import DomainError, dispatch, initialize

logger = logging.getLogger("weekpantry")
ROLE = os.environ.get("FUNCTION_ROLE", "api")


@asynccontextmanager
async def lifespan(app):
    if ROLE == "api" and os.environ.get("PGHOST"):
        await run_in_threadpool(initialize)
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/invoke")
async def invoke(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("input"), dict):
            raise DomainError("Invalid JSON request.")
        payload = body["input"]
        if ROLE == "web":
            if payload.get("action") != "page":
                raise DomainError("Page not found.", 404)
            return {"html": Path(__file__).with_name("index.html").read_text()}
        return await run_in_threadpool(dispatch, payload)
    except (ValueError, DomainError) as exc:
        status = exc.status if isinstance(exc, DomainError) else 422
        return JSONResponse({"error": "CONFLICT" if status == 409 else "INVALID_INPUT",
                             "message": str(exc) if isinstance(exc, DomainError) else "Invalid JSON request."},
                            status_code=status, headers={"X-NanoFaaS-Function-Status": "true"})
    except psycopg.Error:
        logger.exception("Database operation failed; execution=%s", request.headers.get("x-execution-id"))
        # Unmarked: NanoFaaS may retry safely using the durable requestId.
        return JSONResponse({"error": "DATABASE_UNAVAILABLE", "message": "The database is temporarily unavailable."},
                            status_code=503, headers={"Retry-After": "1"})
