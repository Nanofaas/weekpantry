"""Run as a Kubernetes Job; inject credentials without writing or printing them."""
import json
import os
from pathlib import Path

import httpx


def register_functions():
    image = os.environ["FUNCTION_IMAGE"]
    specs = json.loads(Path(__file__).with_name("function-specs.json").read_text())
    with httpx.Client(base_url=os.environ.get("NANOFAAS_URL", "http://control-plane:8080"),
                      timeout=120, trust_env=False) as client:
        for spec in specs:
            spec["image"] = image
            if spec["name"] == "weekpantry-api":
                spec["env"]["PGPASSWORD"] = os.environ["APP_PASSWORD"]
            existing = client.get(f"/v1/functions/{spec['name']}")
            if existing.status_code == 200:
                live = existing.json()
                unchanged = all(live.get(k) == spec.get(k) for k in
                                ("image", "env", "timeoutMs", "concurrency", "maxRetries",
                                 "queueSize", "runtimeMode", "resources"))
                unchanged = unchanged and all(live.get("scalingConfig", {}).get(k) == value
                                              for k, value in spec["scalingConfig"].items())
                if unchanged and live.get("effectiveExecutionMode") == "DEPLOYMENT" and live.get("deploymentBackend") == "k8s":
                    print(f"{spec['name']}: already registered", flush=True)
                    continue
                # Image/env are immutable in PATCH. Replace only our two app functions.
                removed = client.delete(f"/v1/functions/{spec['name']}")
                if removed.status_code != 204:
                    raise RuntimeError(f"Cannot replace {spec['name']}: HTTP {removed.status_code}")
            elif existing.status_code != 404:
                raise RuntimeError(f"Cannot look up {spec['name']}: HTTP {existing.status_code}")
            result = client.post("/v1/functions", json=spec)
            if result.status_code != 201:
                # Never print a returned manifest: it may contain a credential.
                raise RuntimeError(f"Cannot register {spec['name']}: HTTP {result.status_code}")
            live = result.json()
            if live.get("effectiveExecutionMode") != "DEPLOYMENT" or live.get("deploymentBackend") != "k8s":
                raise RuntimeError(f"{spec['name']} is not managed by the k8s backend")
            print(f"{spec['name']}: DEPLOYMENT on k8s", flush=True)


if __name__ == "__main__":
    register_functions()
