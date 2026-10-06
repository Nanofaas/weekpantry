# NanoFaaS developer notes — WeekPantry

These notes record building a meaningful household web app using NanoFaaS,
including frontend delivery, an external database and k3s deployment. The initial
Italian prototype was named Dispensa; it was later extracted and translated as
WeekPantry. Findings refer to the documentation and images actually tested, not every
NanoFaaS version. The initial cached image was later replaced and the main
findings rechecked against source revision `4f3d58b7`; see the final section.

## Priorities

| Priority | Evidence | Proposed improvement |
| --- | --- | --- |
| Blocking in the fresh build at `4f3d58b7` | Restart fails with explicit resources in the catalog | Catalog round-trip tests for every optional field; fix serialisation |
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

The public repository was created at https://github.com/Nanofaas/weekpantry.
A fresh clone built the function image, passed all 14 integration tests without
skips and deployed successfully using the pinned upstream chart. Both functions
reported `already registered`; no definition replacement was needed. Results
are in [fresh-clone-results.json](evidence/fresh-clone-results.json) and
[redeploy-output.txt](evidence/redeploy-output.txt).


## 2026-10-06 — revalidation after identifying the old lab image

Reusing the pre-existing image made the initial runtime observations insufficient
as evidence about the current platform. Its Docker creation time was
2026-09-30T14:43:03+02:00 and it had no OCI revision label; the recipe tag and
version 0.22.0 did not identify its source commit. The initial findings remain
historical evidence and are not upgraded merely by reading current source.

GitHub main and the local checkout both pointed to
`4f3d58b73a9cc3a81ba069ea21806e2e57c3857f`. A separate tracked-source build
excluded agent and Claude files and left the NanoFaaS checkout unchanged.
Gradle built the JVM control plane with modules `k8s-deployment-provider`,
`async-queue` and `runtime-config`. The new image carries the full commit in
`org.opencontainers.image.revision`:

- Image: `127.0.0.1:5000/weekpantry/nanofaas-control-plane:4f3d58b7-20261006`.
- Registry digest: `sha256:60e6fe3b7f1e7dc3ce47c3259aaef9e9a2f346bee0771bb0344d725cca691841`.
- Docker creation time: 2026-10-06T18:55:08+02:00.
- Reported application version: still 0.22.0, which is not a build identity.

### Results and scope

| Finding | Revalidation | Scope of the conclusion |
| --- | --- | --- |
| Catalog restart with explicit resources | Fresh image: registration 201, invocation 200, restart fails on `ResourceSpec.requestWithinLimit` | Reproduced in the JVM build of this exact commit |
| POST invocation / JSON envelope | Current core OpenAPI explicitly specifies InvocationResponse rather than raw output | Still part of the current published contract |
| Literal env values / no Secret reference fields | Current FunctionSpec uses string-valued env; core OpenAPI has no envFrom/secretKeyRef | Still a limitation of the current public function contract |
| Image/env update through PATCH | Current FunctionUpdateRequest accepts only concurrency, timeout, retries and concurrencyControl | Still restricted in current source and OpenAPI |
| Fragmented manifest/runtime documentation | Current function-definition and k8s guides retain the gaps and conflicting Secret/restartPolicy claims | Documentation observation, independent of the old image |
| Chart availability | Earlier authenticated upstream check found a private source repository and no chart release asset at the tested version | Distribution observation; not a runtime defect |
| External DB idempotency | App mutation ledger and replay remain necessary across gateway restarts | Application responsibility; a documentation/example opportunity |

The catalog test used a separate `weekpantry-audit` namespace, no app database
and a single frontend function with explicit requests/limits. Only the test
control plane was deliberately restarted into the failure. Sanitised results
and trace are in [current-catalog-results.json](evidence/current-catalog-results.json)
and [current-catalog-failure.txt](evidence/current-catalog-failure.txt).
The temporary namespace was removed after collecting evidence.

The same fresh image replaced WeekPantry's control plane while retaining the
LimitRange workaround and external PostgreSQL. Both functions were already
registered; their definitions and the database were preserved. The current lab
override now identifies the source-built image. Earlier English-app backend
snapshots are retained under `evidence/initial-weekpantry`; initial Italian
prototype evidence remains under `evidence/initial-dispensa`.

After the update, both deployed-browser checks passed, including week navigation
and cross-week overwrite regressions. Restarting PostgreSQL, API, frontend and
control plane preserved data and shopping checks; durable replay preserved a
later edit. All five application pods were Ready, and a new frontend execution
ID confirmed that HTML still traversed NanoFaaS. Current snapshots are in
[browser-results.json](evidence/browser-results.json),
[persistence-results.json](evidence/persistence-results.json) and
[deployment.json](evidence/deployment.json). The 14 Python application tests were
not repeated for this platform-only image change; their earlier Python 3.12
results remain dated evidence. No NanoFaaS product source was modified.
