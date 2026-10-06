#!/usr/bin/env bash
# Existing k3s cluster; images must already be pushed to an accessible registry.
set -euo pipefail
app_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
: "${FUNCTION_IMAGE:?Set FUNCTION_IMAGE to a pushed immutable image reference}"
: "${EDGE_IMAGE:?Set EDGE_IMAGE to a pushed immutable image reference}"
kubectl_bin="${KUBECTL_BIN:-kubectl}"
helm_bin="${HELM_BIN:-helm}"
node_port="${NODE_PORT:-30188}"
if [[ ! "$node_port" =~ ^[0-9]+$ ]] || (( node_port < 30000 || node_port > 32767 )); then
    echo 'NODE_PORT must be between 30000 and 32767' >&2
    exit 1
fi
task_tmp="$(mktemp -d)"
trap 'rm -rf "$task_tmp"' EXIT
chmod 700 "$task_tmp"
# Fetch only the upstream chart, pinned to the revision used by this app.
# A local chart can be supplied for offline deployments.
chart="${NANOFAAS_CHART:-}"
if [[ -z "$chart" ]]; then
    nanofaas_ref="${NANOFAAS_REF:-4f3d58b73a9cc3a81ba069ea21806e2e57c3857f}"
    if [[ ! "$nanofaas_ref" =~ ^[0-9a-f]{40}$ ]]; then
        echo 'NANOFAAS_REF must be a full upstream commit SHA' >&2
        exit 1
    fi
    mkdir "$task_tmp/chart"
    if ! curl --fail --location --silent --show-error \
        "https://codeload.github.com/miciav/nanofaas/tar.gz/$nanofaas_ref" \
        -o "$task_tmp/nanofaas.tar.gz"; then
        # The current upstream source repository is private. Do not vendor it.
        if ! command -v gh >/dev/null; then
            echo 'Chart download needs upstream access: authenticate gh or set NANOFAAS_CHART' >&2
            exit 1
        fi
        echo 'Downloading the upstream chart with authenticated GitHub access'
        gh api "repos/miciav/nanofaas/tarball/$nanofaas_ref" > "$task_tmp/nanofaas.tar.gz"
    fi
    tar -xzf "$task_tmp/nanofaas.tar.gz" --wildcards --strip-components=3 \
        -C "$task_tmp/chart" '*/deploy/helm/nanofaas/*'
    chart="$task_tmp/chart/nanofaas"
    # Resolve locked dependencies even with an empty local Helm configuration.
    # Keep repository configuration and cache isolated from the user's Helm setup.
    mkdir "$task_tmp/helm-cache"
    helm_repo_args=(--repository-config "$task_tmp/helm-repositories.yaml"
                    --repository-cache "$task_tmp/helm-cache")
    "$helm_bin" repo add prometheus-community \
        https://prometheus-community.github.io/helm-charts "${helm_repo_args[@]}"
    "$helm_bin" dependency build "$chart" "${helm_repo_args[@]}"
fi
"$kubectl_bin" create namespace weekpantry --dry-run=client -o yaml | "$kubectl_bin" apply -f -
if ! "$kubectl_bin" -n weekpantry get secret weekpantry-db >/dev/null 2>&1; then
    python3 - "$task_tmp" <<'PY'
import pathlib, secrets, sys
for name in ('admin-password', 'app-password'):
    path = pathlib.Path(sys.argv[1]) / name
    path.write_text(secrets.token_urlsafe(32))
    path.chmod(0o600)
PY
    "$kubectl_bin" -n weekpantry create secret generic weekpantry-db \
        --from-file="admin-password=$task_tmp/admin-password" \
        --from-file="app-password=$task_tmp/app-password"
fi
helm_args=(upgrade --install weekpantry "$chart"
    --namespace weekpantry -f "$app_dir/helm-values.yaml" --wait --timeout 240s)
if [[ -n "${CONTROL_PLANE_VALUES:-}" ]]; then
    helm_args+=(-f "$CONTROL_PLANE_VALUES")
fi
"$helm_bin" "${helm_args[@]}"
python3 - "$app_dir" "$task_tmp" "$node_port" <<'PY'
import os, pathlib, sys
source, target = map(pathlib.Path, sys.argv[1:3])
for name in ('k8s.yaml', 'register-job.yaml'):
    content = (source / name).read_text().replace('weekpantry/function:dev', os.environ['FUNCTION_IMAGE']).replace('weekpantry/edge:dev', os.environ['EDGE_IMAGE'])
    if name == 'k8s.yaml':
        content = content.replace('nodePort: 30188', 'nodePort: ' + sys.argv[3])
    (target / name).write_text(content)
PY
"$kubectl_bin" apply -f "$task_tmp/k8s.yaml"
"$kubectl_bin" -n weekpantry rollout status statefulset/postgres --timeout=180s
"$kubectl_bin" -n weekpantry delete job weekpantry-register --ignore-not-found
"$kubectl_bin" apply -f "$task_tmp/register-job.yaml"
if ! "$kubectl_bin" -n weekpantry wait --for=condition=complete job/weekpantry-register --timeout=300s; then
    "$kubectl_bin" -n weekpantry logs job/weekpantry-register
    exit 1
fi
"$kubectl_bin" -n weekpantry logs job/weekpantry-register
"$kubectl_bin" -n weekpantry rollout status deployment/fn-weekpantry-web --timeout=180s
"$kubectl_bin" -n weekpantry rollout status deployment/fn-weekpantry-api --timeout=180s
"$kubectl_bin" -n weekpantry rollout status deployment/weekpantry-edge --timeout=180s
echo "WeekPantry ready: http://<k3s-node-ip>:$node_port"
