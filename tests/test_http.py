"""The edge must invoke NanoFaaS even for HTML and preserve application failures."""
import unittest

import httpx
from fastapi.testclient import TestClient

from edge import app, client
from runtime import app as function_app


class EdgeTest(unittest.TestCase):
    def setUp(self):
        self.requests = []

        def control_plane(request):
            import json
            self.requests.append((request.url.path, json.loads(request.content), dict(request.headers)))
            if request.url.path.endswith("weekpantry-web:invoke"):
                return httpx.Response(200, json={"executionId": "web-123", "status": "success",
                    "output": {"html": "<!doctype html><title>WeekPantry</title>"}})
            return httpx.Response(409, json={"executionId": "api-123", "status": "success",
                "output": {"error": "CONFLICT", "message": "Planned recipe"}, "statusCode": 409},
                headers={"X-NanoFaaS-Function-Status": "true", "X-Execution-Id": "api-123"})

        self.original_transport = client._transport
        client._transport = httpx.MockTransport(control_plane)
        self.browser = TestClient(app)

    def tearDown(self):
        client._transport = self.original_transport

    def test_page_is_obtained_from_nanofaas_and_has_execution_evidence(self):
        response = self.browser.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        self.assertIn("WeekPantry", response.text)
        self.assertEqual(response.headers["x-execution-id"], "web-123")
        self.assertEqual(self.requests[0][0], "/v1/functions/weekpantry-web:invoke")
        self.assertEqual(self.requests[0][1], {"input": {"action": "page"}})

    def test_domain_status_and_idempotency_are_preserved(self):
        response = self.browser.post("/api", json={"action": "delete_recipe", "requestId": "req-456"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["message"], "Planned recipe")
        self.assertEqual(self.requests[0][2]["idempotency-key"], "req-456")
        self.assertEqual(response.headers["x-execution-id"], "api-123")

    def test_unavailable_platform_does_not_fall_back_to_local_html(self):
        def offline(request):
            raise httpx.ConnectError("offline", request=request)
        client._transport = httpx.MockTransport(offline)
        response = self.browser.get("/")
        self.assertEqual(response.status_code, 503)
        self.assertIn("message", response.json())

    def test_malformed_json_and_unknown_routes_are_rejected(self):
        self.assertEqual(self.browser.post("/api", content="{").status_code, 400)
        self.assertEqual(self.browser.get("/v1/functions").status_code, 404)
        self.assertEqual(self.requests, [])

    def test_platform_failure_envelope_is_not_a_successful_page(self):
        client._transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
            "executionId": "failed", "status": "error", "error": {"message": "Failed"}}))
        self.assertEqual(self.browser.get("/").status_code, 502)


class RuntimeTest(unittest.TestCase):
    def test_nonobject_input_is_a_marked_domain_error(self):
        with TestClient(function_app) as browser:
            response = browser.post("/invoke", json={"input": []})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.headers["x-nanofaas-function-status"], "true")


if __name__ == "__main__":
    unittest.main()
