"""Restart only WeekPantry's components and prove durable data and mutation replay."""
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

BASE = os.environ.get("WEEKPANTRY_URL", "http://localhost:8188")
KUBECTL = os.environ.get("KUBECTL_BIN", "kubectl")
WEEK = "2035-03-05"


def run():
    with httpx.Client(base_url=BASE, timeout=40, trust_env=False) as client:
        def api(payload):
            if payload["action"] != "state":
                payload = {"requestId": str(uuid4()), **payload}
            response = client.post("/api", json=payload)
            response.raise_for_status()
            return response.json()

        prior = api({"action": "state", "week": WEEK})
        assert not any(m["day"] == WEEK and m["slot"] == "dinner" for m in prior["meals"]), "Test meal slot occupied"
        recipe_id, request_id = str(uuid4()), str(uuid4())
        original = {"id": recipe_id, "title": "Persistence test " + recipe_id[:8],
                    "category": "vegetarian", "minutes": 10, "servings": 2, "instructions": "Stir.",
                    "ingredients": [{"name": "Persistent ingredient " + recipe_id[:8], "quantity": 100, "unit": "g"}]}
        try:
            api({"action": "save_recipe", "recipe": original, "requestId": request_id})
            changed = {**original, "title": original["title"] + " edited"}
            api({"action": "save_recipe", "recipe": changed})
            api({"action": "set_meal", "day": WEEK, "slot": "dinner", "recipeId": recipe_id, "portions": 3})
            before = api({"action": "state", "week": WEEK})
            item = next(i for i in before["shopping"] if i["name"].startswith("persistent ingredient"))
            api({"action": "check_item", "week": WEEK, "key": item["key"], "quantity": item["quantity"], "checked": True})
            before = api({"action": "state", "week": WEEK})

            def kube(*args):
                subprocess.run([KUBECTL, "-n", "weekpantry", *args], check=True)

            kube("rollout", "restart", "statefulset/postgres")
            kube("rollout", "status", "statefulset/postgres", "--timeout=180s")
            for name in ("fn-weekpantry-api", "fn-weekpantry-web", "nanofaas-control-plane"):
                kube("rollout", "restart", f"deployment/{name}")
                kube("rollout", "status", f"deployment/{name}", "--timeout=180s")
            deadline = time.monotonic() + 45
            while True:
                try:
                    after = api({"action": "state", "week": WEEK})
                    break
                except httpx.HTTPError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(1)
            assert after == before, "Data changed after component restarts"
            api({"action": "save_recipe", "recipe": original, "requestId": request_id})
            assert api({"action": "state", "week": WEEK}) == before, "Replay overwrote a later edit after restart"
            page = client.get("/")
            assert page.status_code == 200 and page.headers.get("x-execution-id")
            result = {"check": "PostgreSQL, API, frontend and control-plane restart",
                      "dataUnchanged": True, "durableReplayPreservedLaterEdit": True,
                      "frontendExecutionId": page.headers["x-execution-id"]}
            target = Path(__file__).resolve().parents[1] / "evidence" / "persistence-results.json"
            target.write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2))
        finally:
            try:
                api({"action": "remove_meal", "day": WEEK, "slot": "dinner"})
                api({"action": "delete_recipe", "id": recipe_id})
            except httpx.HTTPError:
                # Preserve the original failure when the control plane itself is offline.
                print("Cleanup pending: restore the platform, then remove the temporary persistence recipe.")


if __name__ == "__main__":
    run()
