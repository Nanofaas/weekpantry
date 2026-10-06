"""Real browser CRUD/planning/shopping smoke through the deployed NanoFaaS edge."""
import json
import os
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from playwright.sync_api import sync_playwright

BASE = os.environ.get("WEEKPANTRY_URL", "http://localhost:8188")
EVIDENCE = Path(__file__).resolve().parents[1] / "evidence"


def run():
    EVIDENCE.mkdir(exist_ok=True)
    day = date(2040, 6, 1)
    test_week = (day - timedelta(days=day.weekday())).isoformat()
    title = "Browser recipe " + str(uuid4())[:8]
    recipe_id = None
    results, page_errors = [], []
    with httpx.Client(base_url=BASE, timeout=40, trust_env=False) as client:
        def api(payload):
            if payload["action"] != "state":
                payload = {**payload, "requestId": str(uuid4())}
            response = client.post("/api", json=payload)
            response.raise_for_status()
            return response.json()

        before = api({"action": "state", "week": test_week})
        assert not any(m["day"] == test_week and m["slot"] == "dinner" for m in before["meals"]), "Test meal slot occupied"
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=os.environ.get("CHROME_BIN", "/usr/bin/google-chrome"),
                                             headless=True, args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 1440, "height": 1100})
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                response = page.goto(BASE)
                assert response.status == 200 and response.headers.get("x-execution-id")
                results.append({"check": "HTML served by NanoFaaS", "executionId": response.headers["x-execution-id"]})
                page.wait_for_function("() => state.recipes.length > 0")
                assert page.evaluate("() => formatAmount(0.000042)") == "0.000042"
                page.screenshot(path=str(EVIDENCE / "desktop-week.png"), full_page=True)
                page.click("#new-recipe")
                form = page.locator("#recipe-form")
                form.locator('[name="title"]').fill(title)
                form.locator('[name="minutes"]').fill("15")
                form.locator('[name="servings"]').fill("2")
                form.locator('[name="instructions"]').fill("Stir. <img src=x onerror=window.xss=1>")
                form.locator('.ingredient-name').fill("Browser test tomatoes")
                form.locator('.ingredient-quantity').fill("300")
                page.click("#add-ingredient")
                form.locator('.ingredient-name').nth(1).fill("BROWSER test tomatoes")
                form.locator('.ingredient-quantity').nth(1).fill("100")
                form.locator('button[type="submit"]').click()
                page.wait_for_function("() => !document.querySelector('#recipe-dialog').open")
                page.wait_for_function("(title) => state.recipes.some(r=>r.title===title)", arg=title)
                recipe_id = page.evaluate("(title) => state.recipes.find(r=>r.title===title).id", title)
                results.append({"check": "Create recipe with two ingredients", "passed": True})
                page.click('[data-tab="recipes"]')
                page.locator('#search').fill(title)
                card = page.locator('.recipe-card').filter(has_text=title)
                card.get_by_role("button", name="View recipe").click()
                assert "<img" in page.locator('#recipe-details').inner_text()
                assert page.locator('#recipe-details img').count() == 0
                assert page.evaluate("() => window.xss") is None
                page.click('#edit-recipe')
                title += " edited"
                form.locator('[name="title"]').fill(title)
                form.locator('button[type="submit"]').click()
                page.wait_for_function("() => !document.querySelector('#recipe-dialog').open")
                page.locator('#search').fill(title)
                page.locator('.recipe-card').filter(has_text=title).get_by_role('button', name='＋ Plan meal').click()
                page.locator('#plan-form [name="day"]').fill(test_week)
                page.locator('#plan-form [name="slot"]').select_option('dinner')
                page.locator('#plan-form [name="portions"]').fill('3')
                page.locator('#plan-form button[type="submit"]').click()
                page.wait_for_function("(week) => state.week===week && state.meals.some(m=>m.slot==='dinner')", arg=test_week)
                page.click('[data-tab="shopping"]')
                row = page.locator('.shopping-row').filter(has_text='browser test tomatoes')
                assert "600 g" in row.inner_text()
                row.locator('input').check()
                page.wait_for_function("() => state.shopping.some(i=>i.name==='browser test tomatoes' && i.checked)")
                page.reload()
                page.wait_for_function("() => state.recipes.length > 0")
                # Select the desired week through the app's navigation state, then reload its data.
                page.evaluate("(value) => { week=value; load(); }", test_week)
                page.wait_for_function("(value) => state.week===value", arg=test_week)
                page.click('[data-tab="shopping"]')
                assert page.locator('.shopping-row').filter(has_text='browser test tomatoes').locator('input').is_checked()
                results.append({"check": "Edit, plan three portions, merge ingredient names and persist shopping check", "passed": True})
                # A planned recipe cannot be deleted: verify user-visible error in its dialog.
                page.evaluate("(id) => detail(id)", recipe_id)
                page.on('dialog', lambda dialog: dialog.accept())
                page.click('#delete-recipe')
                page.wait_for_function("() => document.querySelector('#detail-dialog .form-error').textContent.length > 0")
                assert page.locator('#detail-dialog').is_visible()
                assert "meal plan" in page.locator('#detail-dialog .form-error').inner_text()
                page.locator('#detail-dialog [data-close]').click()
                results.append({"check": "Deletion conflict is visible; HTML in instructions is escaped", "passed": True})
                page.click('[data-tab="week"]')
                page.locator(f'[data-day="{test_week}"][data-slot="dinner"]').click()
                page.click('#remove-meal')
                page.wait_for_function("() => !document.querySelector('#meal-dialog').open")
                page.wait_for_function("(id) => !state.meals.some(m=>m.recipeId===id)", arg=recipe_id)
                page.evaluate("(id) => detail(id)", recipe_id)
                page.click('#delete-recipe')
                page.wait_for_function("() => !document.querySelector('#detail-dialog').open")
                results.append({"check": "Remove meal and delete recipe", "passed": True})
                recipe_id = None
                page.click('#this-week')
                page.wait_for_function("() => state.week === monday(iso(new Date()))")
                page.click('[data-tab="recipes"]')
                page.locator('#search').fill('')
                page.screenshot(path=str(EVIDENCE / 'desktop-recipes.png'), full_page=True)
                page.click('[data-tab="shopping"]')
                page.screenshot(path=str(EVIDENCE / 'desktop-shopping.png'), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.click('[data-tab="week"]')
                page.screenshot(path=str(EVIDENCE / 'mobile-week.png'), full_page=True)
                assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
                assert not page_errors, page_errors
                results.append({"check": "Mobile viewport 390px, no page overflow or JavaScript errors", "passed": True})
                browser.close()
        finally:
            if recipe_id:
                api({"action": "remove_meal", "day": test_week, "slot": "dinner"})
                api({"action": "delete_recipe", "id": recipe_id})
        (EVIDENCE / 'browser-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    run()
