"""HTTP adaptation only: every page and API operation goes through NanoFaaS."""
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
client = httpx.Client(base_url=os.environ.get("NANOFAAS_URL", "http://control-plane:8080"),
                      timeout=35, follow_redirects=False, trust_env=False)


def call_function(name, payload):
    headers = {}
    if payload.get("requestId"):
        headers["Idempotency-Key"] = str(payload["requestId"])
    try:
        upstream = client.post(f"/v1/functions/{name}:invoke", json={"input": payload}, headers=headers)
    except httpx.HTTPError:
        return JSONResponse({"message": "NanoFaaS is unavailable. Try again shortly."}, status_code=503)
    response_headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if upstream.headers.get("retry-after"):
        response_headers["Retry-After"] = upstream.headers["retry-after"]
    try:
        envelope = upstream.json()
        if not isinstance(envelope, dict):
            raise ValueError("Invalid envelope")
    except ValueError:
        return JSONResponse({"message": "Invalid response from NanoFaaS."}, status_code=502)
    execution_id = envelope.get("executionId") or upstream.headers.get("x-execution-id")
    if execution_id:
        response_headers["X-Execution-Id"] = str(execution_id)
    if "output" not in envelope or envelope.get("status") != "success":
        return JSONResponse({"message": "NanoFaaS could not complete the request. Try again shortly."},
                            status_code=upstream.status_code if upstream.status_code >= 400 else 502,
                            headers=response_headers)
    output = envelope["output"]
    if name == "weekpantry-web" and upstream.status_code < 400:
        if not isinstance(output, dict) or not isinstance(output.get("html"), str):
            return JSONResponse({"message": "The frontend function returned an invalid page."}, status_code=502)
        response_headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return HTMLResponse(output["html"], headers=response_headers)
    return JSONResponse(output, status_code=upstream.status_code, headers=response_headers)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def page():
    return call_function("weekpantry-web", {"action": "page"})


@app.post("/api")
async def api(request: Request):
    raw = await request.body()
    if len(raw) > 65536:
        return JSONResponse({"message": "The request is too large."}, status_code=413)
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"message": "Invalid JSON request."}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"message": "Invalid JSON request."}, status_code=400)
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(call_function, "weekpantry-api", payload)
