# NanoFaaS developer notes — WeekPantry

These notes record building a meaningful household web app using NanoFaaS,
including frontend delivery, an external database and k3s deployment. The initial
Italian prototype was named Dispensa; it was later extracted and translated as
WeekPantry. Findings refer to the documentation and lab image actually tested,
not every NanoFaaS version.

## Priorities

| Priority | Evidence | Proposed improvement |
| --- | --- | --- |
| Blocking in the tested image | Restart fails with explicit resources in the catalog | Catalog round-trip tests for every optional field; fix serialisation |
| High for web apps | POST invocation and JSON envelope only | Optional raw HTTP binding or an official adapter |
| High for external databases | Secret references described, absent from live OpenAPI | envFrom/secretKeyRef without resolved passwords in the catalog |
| Medium for updates | Image/env cannot be changed through PATCH | Update and reconcile the full definition |
| Medium for new developers | Manifest and runtime details spread across guides | A complete web app + database + existing-k3s tutorial |

## 2026-10-06 — initial exploration

Sources read: the platform README, docs/README.md, quickstart.md,
tutorial-function.md, function-definition.md, k8s.md,
function-pod-architecture.md, control-plane.md, core OpenAPI and the Helm chart
README. Repository agent and Claude files were not read.

1. **HTTP frontend: missing functionality for this use case.** OpenAPI explicitly
   says POST `:invoke` always returns InvocationResponse, never a raw body.
   There is no browser-facing GET route for HTML. A function can produce the
   frontend, but an external adapter must turn its output into a browser page.
   Proposal: optional function HTTP bindings (method/path and raw response),
   or an official gateway example with unwrapping, error handling and assets.

2. **Incomplete function-definition reference: documentation.** The manifest
   guide lists only a few fields. env, resources, maxRetries, scaling and modes
   require OpenAPI and other guides. Proposal: a complete reference, an editor
   JSON schema and a commented, actually deployable manifest.

3. **Kubernetes Secret references: a promise to verify.** k8s.md mentions Secret
   references; OpenAPI exposes only literal env strings and imagePullSecrets.
   There is no DB-password example or envFrom/valueFrom contract. An application
   developer should not have to assume an undocumented feature exists.

4. **Quickstart and environment: documentation.** The getting-started guide uses
   NanoLab for provisioning/validation. An app author also needs a short path
   for an existing cluster: Helm → external DB → function → invocation → update.

5. **Retries and external state: documentation.** Backoff and invocation
   idempotency are well documented, but an app with DB transactions and
   repeatable mutations is missing. A write can commit before a network error;
   idempotency must also live in the DB, beyond gateway memory.

Environment: Docker was available but the local minikube context was unreachable.
The existing Multipass VM `nanofaas-stack` had a Ready k3s cluster and NanoFaaS in
namespace `nanofaas-e2e`. The prototype used a separate `dispensa` namespace.

Pre-edit graph analysis could not find the new app (risk UNKNOWN). A text search
confirmed no existing application or consumers to change. Graph queries found
managed-deployment/reconcile processes; the index was three commits behind.
No platform symbols or execution flows were modified.

## Initial implementation status

- Domain, frontend, adapter, manifests and repeatable deploy implemented.
- Real k3s deployment with two DEPLOYMENT functions on the k8s backend.
- 14 tests passed, including real PostgreSQL and mutation replay.
- Browser checks passed for CRUD, planning, aggregation, checkboxes, reload,
  deletion conflicts and escaped HTML; mobile viewport checked.
- Initial source files were confined to apps/dispensa, without a platform commit.

## 2026-10-06 — implementation and real deployment

6. **Application updates: functionality.** FunctionUpdateRequest supports
   concurrency, timeout, retries and concurrencyControl; it does not update
   image, env, resources, queueSize or scalingConfig. Image updates required
   DELETE and POST for both functions. External DB state survives, but there
   is downtime and in-memory executions may be lost. Proposal: explicit
   definition updates with reconciliation and documented progress reporting.

7. **Secret limitation confirmed in the live contract.** The cluster's 0.22.0
   OpenAPI has no secretKeyRef/envFrom. A registration Job reads the Kubernetes
   Secret and inserts the password into function env, which is persisted in the
   platform catalog. Proposal: Secret references without resolving values in
   the control plane, a backend-neutral contract and credential-free manifests.

8. **Runtime guides need better links: documentation.** Returning a domain 422
   or 409 without retries requires `X-NanoFaaS-Function-Status: true`.
   OpenAPI describes it and the tutorial mentions it, but there is no complete
   Python domain-error example. This app implements the public HTTP contract
   with FastAPI. Proposal: a Python tutorial with validation, status, headers
   and an external DB; explain when a watchdog is needed and when `/invoke`
   plus `/health` is sufficient.

9. **Inconsistent Kubernetes guide: documentation.** k8s.md describes
   `restartPolicy: Never` for a Deployment. Actual Deployments use
   `restartPolicy: Always`, as Kubernetes requires. The guide seems to mix
   one-shot execution with warm instances.

10. **Local registry: documentation.** The lab registry is reachable as
    127.0.0.1:5000 on the nodes. NanoFaaS validates images through the Kubernetes
    backend, so the control-plane pod does not have to reach that loopback
    address itself. This matters to an app author; local.md only shows Docker.
    Proposal: a k3s registry/mirror/immutable-tag example, with separate paths
    for public registries, private registries and pre-imported images.

What worked well: the HTTP contract enables a simple runtime without a mandatory
SDK; NanoFaaS creates Deployment and Service resources for each function;
application status and execution IDs cross the gateway correctly. With the
workaround below, the catalog PVC allows control-plane restarts without
registering the app again.

Application choices: two functions, an asset-free adapter and a non-superuser
DB account. Embedded HTML/CSS/JS avoid one function per asset, but each visit
invokes the web function and transfers the document in an envelope. A caching
or ETag example would help. The mutation ledger has no automatic expiry; that
is a demo choice, not a NanoFaaS responsibility.

Review found three **application bugs**, separate from platform limitations:
a checkbox targeting the newly requested week during a slow fetch, cross-week
meal replacement without confirmation, and incomplete deploy reconciliation.
All were reproduced and fixed; both week-navigation browser regressions passed
against the actual cluster.

An environment obstacle: Multipass installed through Snap could not transfer
files from the host's /tmp. Image archives were transferred from the project
directory and removed afterwards. The cluster was not reconfigured.

External references used for technical choices:
[psycopg transactions](https://www.psycopg.org/psycopg3/docs/basic/usage.html),
[FastAPI direct responses](https://fastapi.tiangolo.com/advanced/response-directly/),
[Kubernetes persistent volumes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/).

## 2026-10-06 — a defect found by restart testing

11. **Unreadable catalog with explicit resources: blocking in the tested image.**
    PostgreSQL and both functions restarted successfully. Restarting the control
    plane then failed: its own catalog contained `resources.requestWithinLimit`.
    Jackson rejected that unknown ResourceSpec property and Spring could not
    start. The exact image and sanitised trace are in
    [resource-catalog-failure.txt](evidence/initial-dispensa/resource-catalog-failure.txt).
    This demonstrates a defect in that lab image reporting version 0.22.0;
    the current platform source and other images were not tested for this defect.

    App workaround: omit `resources` in FunctionSpec and apply a Kubernetes
    namespace `LimitRange`. Recovery stopped only the prototype control plane,
    backed up its catalog privately on the same PVC and cleared resources in
    its two function definitions. Platform code, other namespaces and the DB
    were not changed. The control plane returned to Ready.
    The backup remains on that PVC with mode 0600; it is not in this repository
    because it contains confidential environment variables.

    Proposal: an end-to-end restart test covering every optional field,
    especially resources, plus a catalog serialisation round-trip. Format errors
    should identify the property and have a documented recovery procedure.
    A stateless function still depends on control-plane catalog state; function
    registration persistence needs testing separately from app DB persistence.

## Initial verification result

With the workaround, PostgreSQL, API, frontend and control-plane restarts passed
without data changes. Replaying a pre-restart mutation preserved a later edit.
An identical deploy reported `already registered` for both functions.
All 14 tests passed inside the Python 3.12 Alpine function image with real
PostgreSQL, and both browser checks passed after the full restart.

Independent review rechecked the three fixes and LimitRange workaround without
further blockers. The pre-existing `nanofaas-e2e` namespace was not changed.

GitNexus `detect-changes --scope all` found no tracked platform changes. The new
app was still untracked, so that result did **not** validate its graph. No
initial platform commit or agent-file changes were made. HTTP/DB tests,
review and actual k3s invocations provided the app-level evidence.

## 2026-10-06 — extraction as WeekPantry

The application belongs in a separate example repository: it has its own
release lifecycle, dependencies and database. Keeping it inside NanoFaaS would
mix platform work with household-app changes.

The standalone version translates UI and accessibility labels, dates and
numbers, domain errors, sample recipes, wire values, tests and documentation.
Deployment downloads a pinned chart instead of depending on `../../deploy`.
A selectable NodePort lets the English app run beside the original experiment.
The original namespace and its database are preserved; no automatic migration
from the Italian schema is attempted.

The original evidence is retained under `evidence/initial-dispensa`, including
its unchanged technical identifiers and historical Italian screenshots. New
English screenshots and verification results belong directly in `evidence/`.
The README documents deployment from a clean clone without a NanoFaaS checkout.

A further documentation gap became visible during extraction: there was no
release asset for the chart at the tested version. The example therefore pins
an upstream commit and builds the locked chart dependencies. A published,
versioned Helm chart would simplify independent applications and offline setup.

The anonymous chart download returned 404 even though the commit was confirmed
through GitHub API. Repository metadata confirmed that the platform source is
private. The default control-plane image is publicly pullable, but the chart
needs authenticated GitHub access or a supplied local directory. The deploy
script falls back to `gh api` and the README makes this prerequisite explicit.
Private platform source is not copied into the public example. A public chart
distribution would remove this barrier for developers trying the example.

Helm dependency build initially failed on a clean local configuration because
prometheus-community was not registered. The deploy now creates temporary Helm
repository configuration and cache before resolving dependencies. It leaves
the user's existing Helm repository settings untouched.

English verification passed: all 14 tests inside Python 3.12 Alpine with real
PostgreSQL, both deployed-browser checks, and restarts of PostgreSQL, API,
frontend and control plane. Data and checkbox state were unchanged, and replay
preserved a later edit. English desktop/mobile screenshots and execution IDs
are recorded in evidence. The new lab uses namespace `weekpantry` and port
30189, preserving both previous namespaces.

The independent extraction review found no remaining Critical or Important
issues once English evidence was complete. GitNexus indexed the standalone
app with 3 execution flows. Before its first commit, `scope all` could not run
because an unborn repository has no HEAD; `scope staged` analysed all files
being committed and reported medium risk across dispatch/aggregation/date/UUID
flows. Its CLI abbreviates the displayed symbol list. The new app has no
platform callers; script invocations in the README were checked explicitly
because file impact remained UNKNOWN. A clean `scope all` check is also run
after the initial commit.
