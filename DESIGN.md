# WeekPantry — design and implementation decisions

## Purpose

A household app for keeping recipes, organising the week's lunches and dinners,
and deriving a shopping list from planned servings. It also tests what building
an application with NanoFaaS feels like using its public documentation and HTTP
contracts. The initial development did not read repository AGENTS.md, CLAUDE.md
or the .claude directory.

WeekPantry is a standalone example in the NanoFaaS organisation. Its name joins
weekly planning with a household pantry. All user-facing text, sample data,
application errors and documentation are in English.

## Architecture

- Python 3.12, FastAPI and psycopg; plain HTML/CSS/JavaScript without a frontend build.
- PostgreSQL separate from NanoFaaS, with a StatefulSet and PVC on k3s.
- Two functions, `weekpantry-web` and `weekpantry-api`, both NanoFaaS-managed
  DEPLOYMENT functions. The web function returns HTML with embedded CSS and JS.
- An HTTP adapter exposes GET `/` and POST `/api`, translates them into NanoFaaS
  invocations and unwraps the JSON envelope. Its image has no frontend assets;
  it has no DB access and never calls function pods directly.
- Namespace `weekpantry` and a separate NanoFaaS release isolate the application.
  No changes to platform source code are needed.
- DB credentials are generated into Kubernetes Secrets. Registration reads the
  app password from the Secret and passes it through the documented
  FunctionSpec `env` field. It is absent from this repository, but NanoFaaS
  keeps the resolved password in its catalog. The app DB user is not a superuser.
- The deploy script fetches a pinned upstream chart or accepts a local chart.
  The standalone repository does not copy platform code or need a monorepo checkout.

## Behaviour

Recipes contain title, category, preparation minutes, base servings, instructions
and ingredients (name, amount, unit). Create, edit and delete recipes; deletion
of a planned recipe returns a conflict. Plan meals by date and lunch/dinner slot,
choose servings and browse weeks. Planning over an existing meal asks for
confirmation, including when the target is in another week.

Shopping amounts are grouped by normalised ingredient name and unit, scaled to
servings. Different units are not converted. Checkboxes persist per week and
required quantity; a changed quantity resets the check. Five initial recipes
are seeded once without restoring deleted recipes later.

Mutations use client-generated UUID request IDs and a transactionally written
ledger. Replaying a request returns its recorded result; it does not overwrite
a later edit. Reusing the same ID with a different payload is a conflict.
Reads use a consistent transaction snapshot.

Validate ISO dates from 2000 to 2100, UUIDs, finite quantities, integer servings,
text lengths and recipe structure. Ingredient quantities range from 0.001 to
100000, so scaled amounts do not round to zero. Domain errors are marked with
`X-NanoFaaS-Function-Status`; DB connection errors return an unmarked 503 so
NanoFaaS can safely retry the request.

## Implementation and verification

1. Domain tests cover serving arithmetic, distinct units, validation, real
   PostgreSQL CRUD/planning/shopping and repeated mutations.
2. The schema, sample data, domain and HTTP runtime run without compiling
   NanoFaaS or installing its SDK.
3. The accessible frontend handles loading, failures, keyboard tabs, dialogs,
   confirmation and a mobile viewport. Recipe text is escaped before rendering.
4. Kubernetes manifests and repeatable deployment register and verify both
   functions on the k8s backend. An unchanged deployment preserves definitions.
5. Real browser flows and full component restarts verify the deployed app.
   Sanitised evidence and developer notes travel with the repository.

## Repository extraction plan

1. Copy the app into an isolated repository and retain its MIT license.
2. Translate the interface, validation messages, recipes, tests and documents;
   use English wire values and date/number formatting throughout.
3. Replace monorepo-relative paths with repository-relative paths. Download the
   pinned chart for deploys and make the NodePort selectable for existing labs.
4. Deploy the English app in a new namespace and rerun Python 3.12, browser and
   restart checks. Preserve the original lab and its state separately.
5. Publish `Nanofaas/weekpantry` as a public repository, verify a fresh clone,
   then remove the duplicate app directory from the NanoFaaS checkout.

## Conditions to keep checking

Retries after DB commit; double clicks; two browsers changing the same meal;
HTML in recipe text; NaN/infinite quantities; distinct units; weeks crossing
New Year; deleting a recipe while another client plans it; repeated deploys;
unavailable frontend when NanoFaaS is offline; a slow week fetch while a
checkbox is being changed. Tests cover the app's effects at these boundaries.

## Limitations

Shared data without accounts, local-node storage, no automatic ledger pruning
and brief downtime for function image updates are deliberate scope choices.
The resource catalog restart workaround applies to the lab image identified
in the notes, not a claim about all NanoFaaS versions.
