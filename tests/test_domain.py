"""Catch wrong portion arithmetic, lost units, invalid inputs and unsafe retries."""
import os
import unittest
from datetime import date
from uuid import uuid4

import psycopg

from domain import DomainError, Recipe, aggregate_shopping, dispatch, initialize


def recipe_payload(**changes):
    return {"id": str(uuid4()), "title": "Tomato pasta", "category": "vegetarian",
            "minutes": 20, "servings": 2, "instructions": "Cook the pasta.",
            "ingredients": [{"name": "Pasta", "quantity": 200, "unit": "g"},
                            {"name": "Tomatoes", "quantity": 300, "unit": "g"}], **changes}


class DomainTest(unittest.TestCase):
    def test_scales_portions_and_merges_names_but_keeps_units_separate(self):
        rows = [{"servings": 2, "portions": 3, "ingredients": [
            {"name": "  PASTA  ", "quantity": 200, "unit": "g"},
            {"name": "Oil", "quantity": 1, "unit": "tbsp"}]},
            {"servings": 4, "portions": 2, "ingredients": [
                {"name": "pasta", "quantity": 100, "unit": "g"},
                {"name": "Oil", "quantity": 20, "unit": "ml"}]}]
        result = aggregate_shopping(rows)
        self.assertEqual({(r["name"], r["unit"]): r["quantity"] for r in result},
                         {("pasta", "g"): 350, ("oil", "tbsp"): 1.5, ("oil", "ml"): 10})

    def test_rejects_invalid_or_nonfinite_quantities_and_blank_title(self):
        for quantity in [0, -1, 0.000001, float("nan"), float("inf"), True]:
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                Recipe.model_validate(recipe_payload(ingredients=[
                    {"name": "Pasta", "quantity": quantity, "unit": "g"}]))
        with self.assertRaises(ValueError):
            Recipe.model_validate(recipe_payload(title="   "))

    def test_smallest_supported_quantity_is_visible_in_one_portion(self):
        recipe = Recipe.model_validate(recipe_payload(servings=24, ingredients=[
            {"name": "Spice", "quantity": 0.001, "unit": "g"}])).model_dump(mode="json")
        item = aggregate_shopping([{**recipe, "portions": 1}])[0]
        self.assertEqual(item["quantity"], 0.000042)


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Requires dedicated test PostgreSQL")
class DatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ["TEST_DATABASE_URL"]
        initialize(cls.dsn)

    def setUp(self):
        # This database is created only for tests, never point this at WeekPantry.
        with psycopg.connect(self.dsn) as conn:
            conn.execute("TRUNCATE shopping_checks, meals, recipes CASCADE")

    def call(self, **payload):
        if payload.get("action") != "state":
            payload.setdefault("requestId", str(uuid4()))
        return dispatch(payload, self.dsn)

    def test_crud_planning_shopping_retry_and_persistence(self):
        recipe = recipe_payload()
        request_id = str(uuid4())
        first = self.call(action="save_recipe", recipe=recipe, requestId=request_id)
        self.assertEqual(first, self.call(action="save_recipe", recipe=recipe, requestId=request_id))
        self.call(action="set_meal", day="2026-12-31", slot="dinner", recipeId=recipe["id"], portions=3)
        self.call(action="set_meal", day="2026-12-31", slot="dinner", recipeId=recipe["id"], portions=3)
        state = self.call(action="state", week="2026-12-31")
        self.assertEqual(state["week"], "2026-12-28")
        self.assertEqual(len(state["meals"]), 1)
        self.assertEqual(state["shopping"][0]["quantity"], 300)
        item = state["shopping"][0]
        self.call(action="check_item", week=state["week"], key=item["key"], quantity=item["quantity"], checked=True)
        self.assertTrue(self.call(action="state", week=state["week"])["shopping"][0]["checked"])
        self.call(action="set_meal", day="2026-12-31", slot="dinner", recipeId=recipe["id"], portions=4)
        self.assertFalse(self.call(action="state", week=state["week"])["shopping"][0]["checked"])
        recipe["title"] = "New title"
        self.call(action="save_recipe", recipe=recipe)
        self.assertEqual(self.call(action="state", week=state["week"])["recipes"][0]["title"], "New title")
        with self.assertRaises(DomainError) as caught:
            self.call(action="delete_recipe", id=recipe["id"])
        self.assertEqual(caught.exception.status, 409)
        self.call(action="remove_meal", day="2026-12-31", slot="dinner")
        self.call(action="delete_recipe", id=recipe["id"])
        self.call(action="delete_recipe", id=recipe["id"])
        self.assertEqual(self.call(action="state", week=state["week"])["recipes"], [])

    def test_unknown_recipe_bad_dates_and_unknown_actions_are_domain_errors(self):
        for payload, status in [
            ({"action": "set_meal", "day": "2026-10-06", "slot": "lunch", "recipeId": str(uuid4()), "portions": 2}, 404),
            ({"action": "state", "week": "not-a-date"}, 422),
            ({"action": "set_meal", "day": "2026-10-06", "slot": "breakfast", "recipeId": str(uuid4()), "portions": 2}, 422),
            ({"action": "unknown"}, 422),
            ({"action": "state", "week": "9999-12-31"}, 422),
        ]:
            with self.subTest(payload=payload), self.assertRaises(DomainError) as caught:
                self.call(**payload)
            self.assertEqual(caught.exception.status, status)

    def test_initialization_does_not_restore_deleted_seed_recipes(self):
        initialize(self.dsn)
        self.assertEqual(self.call(action="state", week="2026-10-06")["recipes"], [])

    def test_replayed_mutation_does_not_overwrite_a_later_edit(self):
        recipe = recipe_payload()
        request_id = str(uuid4())
        self.call(action="save_recipe", recipe=recipe, requestId=request_id)
        changed = {**recipe, "title": "Edited"}
        self.call(action="save_recipe", recipe=changed)
        self.call(action="save_recipe", recipe=recipe, requestId=request_id)
        self.assertEqual(self.call(action="state", week="2026-10-06")["recipes"][0]["title"], "Edited")
        with self.assertRaises(DomainError) as caught:
            self.call(action="save_recipe", recipe=changed, requestId=request_id)
        self.assertEqual(caught.exception.status, 409)


if __name__ == "__main__":
    unittest.main()
