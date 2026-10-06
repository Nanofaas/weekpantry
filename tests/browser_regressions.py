"""Exercise cross-week replacement and a checkbox during an in-flight week change."""
import os
from uuid import uuid4

import httpx
from playwright.sync_api import sync_playwright

BASE = os.environ.get("WEEKPANTRY_URL", "http://localhost:8188")
WEEK, NEXT = "2031-04-07", "2031-04-14"


def run():
    with httpx.Client(base_url=BASE, timeout=40, trust_env=False) as client:
        def api(payload):
            if payload["action"] != "state":
                payload = {**payload, "requestId": str(uuid4())}
            response = client.post("/api", json=payload)
            response.raise_for_status()
            return response.json()

        original = {week: api({"action": "state", "week": week}) for week in (WEEK, NEXT)}
        recipe_id = str(uuid4())
        recipe = {"id": recipe_id, "title": "Week navigation test " + recipe_id[:8], "category": "vegetarian",
                  "minutes": 10, "servings": 2, "instructions": "Stir.",
                  "ingredients": [{"name": "Test ingredient " + recipe_id[:8], "quantity": 100, "unit": "g"}]}
        api({"action": "save_recipe", "recipe": recipe})
        failures = []
        try:
            for week in (WEEK, NEXT):
                api({"action": "set_meal", "day": week, "slot": "lunch", "recipeId": recipe_id, "portions": 2})
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=os.environ.get("CHROME_BIN", "/usr/bin/google-chrome"),
                                             headless=True, args=["--no-sandbox"])
                page = browser.new_page()
                page.goto(BASE)
                page.wait_for_function("() => state.recipes.length > 0")
                page.evaluate("() => { week='2031-04-07'; load(); }")
                page.wait_for_function("() => state.week === '2031-04-07'")
                page.evaluate("() => showTab('week')")
                captured, pending = [], []

                def intercept(route):
                    body = route.request.post_data_json
                    if body["action"] == "state" and body["week"] == NEXT and not pending:
                        pending.append(route)
                    else:
                        if body["action"] == "check_item":
                            captured.append(body)
                        route.continue_()

                page.route("**/api", intercept)
                page.click("#next-week")
                page.click("[data-tab=shopping]")
                page.wait_for_timeout(100)
                checkbox = page.locator("#shopping input").filter(visible=True).first
                if not checkbox.is_disabled():
                    checkbox.check()
                    page.wait_for_timeout(300)
                    if captured and captured[0]["week"] != WEEK:
                        failures.append("Shopping checkbox wrote the requested week instead of the displayed week")
                for route in pending:
                    route.abort()
                page.unroute("**/api", intercept)
                page.goto(BASE)
                page.wait_for_function("() => state.recipes.length > 0")
                confirms = []

                def dismiss(dialog):
                    confirms.append(dialog.message)
                    dialog.dismiss()

                page.on("dialog", dismiss)
                page.evaluate("(id) => planRecipe(id)", recipe_id)
                page.locator("#plan-form [name=day]").fill(NEXT)
                page.locator("#plan-form [name=portions]").fill("3")
                page.locator("#plan-form button[type=submit]").click()
                page.wait_for_timeout(700)
                meal = next(m for m in api({"action": "state", "week": NEXT})["meals"]
                            if m["day"] == NEXT and m["slot"] == "lunch")
                if not confirms or meal["portions"] != 2:
                    failures.append("Cross-week planning replaced an occupied slot without confirmation")
                browser.close()
        finally:
            for week in (WEEK, NEXT):
                api({"action": "remove_meal", "day": week, "slot": "lunch"})
                previous = next((m for m in original[week]["meals"] if m["day"] == week and m["slot"] == "lunch"), None)
                if previous:
                    api({"action": "set_meal", "day": week, "slot": "lunch",
                         "recipeId": previous["recipeId"], "portions": previous["portions"]})
            api({"action": "delete_recipe", "id": recipe_id})
        assert not failures, "; ".join(failures)
        print("PASS: week navigation and cross-week overwrite regressions")


if __name__ == "__main__":
    run()
