CREATE TABLE IF NOT EXISTS settings (key text PRIMARY KEY);
CREATE TABLE IF NOT EXISTS recipes (
    id uuid PRIMARY KEY,
    document jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS meals (
    day date NOT NULL,
    slot text NOT NULL CHECK (slot IN ('lunch', 'dinner')),
    recipe_id uuid NOT NULL REFERENCES recipes(id) ON DELETE RESTRICT,
    portions integer NOT NULL CHECK (portions BETWEEN 1 AND 24),
    PRIMARY KEY (day, slot)
);
CREATE TABLE IF NOT EXISTS shopping_checks (
    week date NOT NULL,
    item_key text NOT NULL,
    quantity numeric NOT NULL CHECK (quantity > 0),
    checked boolean NOT NULL,
    PRIMARY KEY (week, item_key)
);
CREATE TABLE IF NOT EXISTS operations (
    id uuid PRIMARY KEY,
    fingerprint text NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
