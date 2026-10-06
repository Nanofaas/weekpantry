import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import register


class RegisterTest(unittest.TestCase):
    def test_replaces_a_function_whose_queue_configuration_changed(self):
        specs = json.loads(Path(register.__file__).with_name("function-specs.json").read_text())
        writes = []

        def platform(request):
            name = request.url.path.rsplit("/", 1)[-1]
            if request.method == "GET":
                live = next(s.copy() for s in specs if s["name"] == name)
                live["image"] = "test/function:1"
                live["env"] = live["env"].copy()
                if name == "weekpantry-api":
                    live["env"]["PGPASSWORD"] = "test-password"
                live.update(effectiveExecutionMode="DEPLOYMENT", deploymentBackend="k8s", queueSize=1)
                return httpx.Response(200, json=live)
            writes.append((request.method, name))
            if request.method == "DELETE":
                return httpx.Response(204)
            body = json.loads(request.content)
            self.assertEqual(body["queueSize"], 100)
            return httpx.Response(201, json={"effectiveExecutionMode": "DEPLOYMENT", "deploymentBackend": "k8s"})

        transport_client = httpx.Client(base_url="http://platform", transport=httpx.MockTransport(platform))
        with patch.dict(os.environ, FUNCTION_IMAGE="test/function:1", APP_PASSWORD="test-password"), \
             patch.object(register.httpx, "Client", return_value=transport_client):
            register.register_functions()
        self.assertEqual([m for m, name in writes], ["DELETE", "POST", "DELETE", "POST"])


if __name__ == "__main__":
    unittest.main()
