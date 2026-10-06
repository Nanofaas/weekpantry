"""Stateless application logic; all durable state and deduplication live in PostgreSQL."""
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Unit = Literal["g", "kg", "ml", "l", "pcs", "tbsp", "tsp"]


class Ingredient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    quantity: float = Field(ge=0.001, le=100000, allow_inf_nan=False)
    unit: Unit

    @field_validator("quantity", mode="before")
    @classmethod
    def numeric_quantity(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Quantity must be a number.")
        return value


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    title: Name
    category: Literal["vegetarian", "meat", "fish", "sweet"]
    minutes: int = Field(strict=True, ge=1, le=600)
    servings: int = Field(strict=True, ge=1, le=24)
    instructions: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]
    ingredients: list[Ingredient] = Field(min_length=1, max_length=40)


class DomainError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def valid_date(value):
    if not isinstance(value, str):
        raise DomainError("Choose a valid date.")
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise DomainError("Choose a valid date.") from None
    if result.isoformat() != value or not date(2000, 1, 3) <= result <= date(2100, 12, 26):
        raise DomainError("The date must be between 2000 and 2100.")
    return result


def week_start(value):
    day = valid_date(value)
    return day - timedelta(days=day.weekday())


def valid_uuid(value):
    try:
        return UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise DomainError("Invalid identifier.") from None


def aggregate_shopping(rows):
    amounts = {}
    for row in rows:
        ratio = Decimal(str(row["portions"])) / Decimal(str(row["servings"]))
        for item in row["ingredients"]:
            name = " ".join(item["name"].split()).casefold()
            key = (name, item["unit"])
            amounts[key] = amounts.get(key, Decimal(0)) + Decimal(str(item["quantity"])) * ratio
    return [{"name": name, "unit": unit, "quantity": round(float(amount), 6),
             "key": hashlib.sha256(json.dumps([name, unit], ensure_ascii=False).encode()).hexdigest()}
            for (name, unit), amount in sorted(amounts.items())]


def initialize(dsn=None):
    with psycopg.connect(dsn or "", connect_timeout=5) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(804061)")
        conn.execute(Path(__file__).with_name("schema.sql").read_text())
        seeded = conn.execute("SELECT 1 FROM settings WHERE key='seed-v1'").fetchone()
        if not seeded:
            for payload in json.loads(Path(__file__).with_name("seed.json").read_text()):
                recipe = Recipe.model_validate(payload).model_dump(mode="json")
                conn.execute("INSERT INTO recipes (id, document) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                             (recipe["id"], Jsonb(recipe)))
            monday = date.today() - timedelta(days=date.today().weekday())
            for offset, slot, suffix in [(0, "lunch", 1), (1, "dinner", 2), (2, "dinner", 3), (4, "dinner", 4)]:
                conn.execute("INSERT INTO meals (day,slot,recipe_id,portions) VALUES (%s,%s,%s,2) ON CONFLICT DO NOTHING",
                             (monday + timedelta(days=offset), slot, f"1ba5d777-b547-4824-88f5-71a9893bade{suffix}"))
            conn.execute("INSERT INTO settings (key) VALUES ('seed-v1')")


def read_state(conn, week):
    recipes = conn.execute("SELECT document FROM recipes ORDER BY document->>'title'").fetchall()
    meals = conn.execute("""SELECT m.day, m.slot, m.recipe_id, m.portions, r.document
        FROM meals m JOIN recipes r ON r.id=m.recipe_id
        WHERE m.day >= %s AND m.day < %s ORDER BY m.day, m.slot""",
                         (week, week + timedelta(days=7))).fetchall()
    shopping = aggregate_shopping([{**m["document"], "portions": m["portions"]} for m in meals])
    checks = {r["item_key"]: r for r in conn.execute(
        "SELECT item_key, quantity, checked FROM shopping_checks WHERE week=%s", (week,))}
    for item in shopping:
        check = checks.get(item["key"])
        item["checked"] = bool(check and check["checked"] and
                               check["quantity"] == Decimal(str(item["quantity"])))
    return {"week": week.isoformat(), "recipes": [r["document"] for r in recipes],
            "meals": [{"day": m["day"].isoformat(), "slot": m["slot"],
                       "recipeId": str(m["recipe_id"]), "portions": m["portions"],
                       "title": m["document"]["title"]} for m in meals], "shopping": shopping}


def mutate(conn, payload):
    action = payload["action"]
    if action == "save_recipe":
        try:
            recipe = Recipe.model_validate(payload.get("recipe")).model_dump(mode="json")
        except ValueError:
            raise DomainError("Check the title, ingredients, quantities, servings and instructions.") from None
        conn.execute("""INSERT INTO recipes (id,document) VALUES (%s,%s)
            ON CONFLICT (id) DO UPDATE SET document=excluded.document""", (recipe["id"], Jsonb(recipe)))
        return {"id": recipe["id"]}
    if action == "delete_recipe":
        recipe_id = valid_uuid(payload.get("id"))
        try:
            conn.execute("DELETE FROM recipes WHERE id=%s", (recipe_id,))
        except psycopg.errors.ForeignKeyViolation:
            raise DomainError("This recipe is in your meal plan. Remove the meals that use it first.", 409) from None
    elif action in ("set_meal", "remove_meal"):
        day = valid_date(payload.get("day"))
        slot = payload.get("slot")
        if slot not in ("lunch", "dinner"):
            raise DomainError("Choose lunch or dinner.")
        if action == "remove_meal":
            conn.execute("DELETE FROM meals WHERE day=%s AND slot=%s", (day, slot))
        else:
            recipe_id = valid_uuid(payload.get("recipeId"))
            portions = payload.get("portions")
            if type(portions) is not int or not 1 <= portions <= 24:
                raise DomainError("Servings must be between 1 and 24.")
            try:
                conn.execute("""INSERT INTO meals (day,slot,recipe_id,portions) VALUES (%s,%s,%s,%s)
                    ON CONFLICT (day,slot) DO UPDATE SET recipe_id=excluded.recipe_id, portions=excluded.portions""",
                             (day, slot, recipe_id, portions))
            except psycopg.errors.ForeignKeyViolation:
                raise DomainError("This recipe no longer exists. Reload the cookbook.", 404) from None
    elif action == "check_item":
        week = week_start(payload.get("week"))
        key, quantity, checked = payload.get("key"), payload.get("quantity"), payload.get("checked")
        if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise DomainError("Invalid ingredient.")
        if type(checked) is not bool:
            raise DomainError("Invalid checkbox value.")
        # Validate against the computed shopping list, including finite quantities.
        item = next((i for i in read_state(conn, week)["shopping"] if i["key"] == key), None)
        if not item or type(quantity) not in (int, float) or quantity != item["quantity"]:
            raise DomainError("The shopping list has changed. Reload it.", 409)
        conn.execute("""INSERT INTO shopping_checks (week,item_key,quantity,checked) VALUES (%s,%s,%s,%s)
            ON CONFLICT (week,item_key) DO UPDATE SET quantity=excluded.quantity,checked=excluded.checked""",
                     (week, key, quantity, checked))
    return {"saved": True}


def dispatch(payload, dsn=None):
    if not isinstance(payload, dict):
        raise DomainError("Invalid JSON request.")
    action = payload.get("action")
    if action not in ("state", "save_recipe", "delete_recipe", "set_meal", "remove_meal", "check_item"):
        raise DomainError("Unknown operation.")
    week = week_start(payload.get("week", date.today().isoformat())) if action == "state" else None
    request_id = valid_uuid(payload.get("requestId")) if action != "state" else None
    try:
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
    except (ValueError, TypeError):
        raise DomainError("The request contains invalid values.") from None
    with psycopg.connect(dsn or "", connect_timeout=5, row_factory=dict_row,
                         options="-c statement_timeout=5000 -c lock_timeout=4000") as conn:
        if action == "state":
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            return read_state(conn, week)
        # Serialize duplicates across every replica; a replay never overwrites a newer edit.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(request_id),))
        previous = conn.execute("SELECT fingerprint,result FROM operations WHERE id=%s", (request_id,)).fetchone()
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise DomainError("This request identifier was already used for another operation.", 409)
            return previous["result"]
        result = mutate(conn, payload)
        conn.execute("INSERT INTO operations (id,fingerprint,result) VALUES (%s,%s,%s)",
                     (request_id, fingerprint, Jsonb(result)))
        return result
