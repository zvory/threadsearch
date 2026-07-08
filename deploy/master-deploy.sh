#!/usr/bin/env bash
set -Eeuo pipefail

log() {
  printf 'master-deploy: %s\n' "$*" >&2
}

fail() {
  log "$*"
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

resolve_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    printf '%s\n' "$PYTHON"
    return
  fi
  if [[ ! -x ".venv/bin/python" ]]; then
    command -v python3 >/dev/null 2>&1 || fail "required command not found: python3"
    log "creating .venv with python3"
    python3 -m venv .venv
  fi
  printf '%s\n' ".venv/bin/python"
}

resolve_flyctl() {
  if [[ -n "${FLYCTL:-}" ]]; then
    printf '%s\n' "$FLYCTL"
    return
  fi
  if command -v flyctl >/dev/null 2>&1; then
    command -v flyctl
    return
  fi
  if command -v fly >/dev/null 2>&1; then
    command -v fly
    return
  fi
  fail "required command not found: flyctl or fly"
}

require_clean_tree() {
  local status
  status="$(git status --short)"
  if [[ -n "$status" ]]; then
    printf '%s\n' "$status" >&2
    fail "working tree must be clean before deploying"
  fi
}

require_master_head() {
  local branch local_head remote_head merge_base

  branch="$(git branch --show-current)"
  [[ "$branch" == "$DEPLOY_BRANCH" ]] || fail "refusing to deploy branch '$branch'; expected '$DEPLOY_BRANCH'"

  require_clean_tree
  git fetch "$DEPLOY_REMOTE" "+refs/heads/$DEPLOY_BRANCH:refs/remotes/$DEPLOY_REMOTE/$DEPLOY_BRANCH"

  local_head="$(git rev-parse HEAD)"
  remote_head="$(git rev-parse "$DEPLOY_REMOTE/$DEPLOY_BRANCH")"
  if [[ "$local_head" == "$remote_head" ]]; then
    return
  fi

  merge_base="$(git merge-base HEAD "$DEPLOY_REMOTE/$DEPLOY_BRANCH")"
  if [[ "$merge_base" == "$local_head" ]]; then
    log "fast-forwarding $DEPLOY_BRANCH to $DEPLOY_REMOTE/$DEPLOY_BRANCH"
    git merge --ff-only "$DEPLOY_REMOTE/$DEPLOY_BRANCH"
    require_clean_tree
    return
  fi

  fail "local $DEPLOY_BRANCH is not exactly $DEPLOY_REMOTE/$DEPLOY_BRANCH; push or reset before deploying"
}

run_public_smoke_if_configured() {
  if [[ -z "${THREAD_SEARCH_PUBLIC_BASE_URL:-}" ]]; then
    log "THREAD_SEARCH_PUBLIC_BASE_URL is not set; skipping live public-smoke"
    return
  fi

  log "running live public-smoke against $THREAD_SEARCH_PUBLIC_BASE_URL"
  "$PYTHON_BIN" -m planquest.cli public-smoke \
    --base-url "$THREAD_SEARCH_PUBLIC_BASE_URL" \
    --require-artifact-manifest \
    --probe Soviet \
    --probe Cuba
}

write_receipt() {
  local receipt_dir receipt_path deployed_at app_name public_base_url

  receipt_dir="${THREAD_SEARCH_DEPLOY_RECEIPT_DIR:-data/deployments}"
  receipt_path="$receipt_dir/master-${DEPLOY_SHA_SHORT}.json"
  deployed_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  app_name="${FLY_APP:-threadsearch}"
  public_base_url="${THREAD_SEARCH_PUBLIC_BASE_URL:-}"

  mkdir -p "$receipt_dir"
  {
    printf '{\n'
    printf '  "deployed_at_utc": "%s",\n' "$deployed_at"
    printf '  "branch": "%s",\n' "$DEPLOY_BRANCH"
    printf '  "commit": "%s",\n' "$DEPLOY_SHA"
    printf '  "fly_app": "%s",\n' "$app_name"
    printf '  "artifact_dir": "%s",\n' "$THREAD_SEARCH_DEPLOY_ARTIFACT_DIR"
    printf '  "bundle_manifest": "%s",\n' "$THREAD_SEARCH_DEPLOY_BUNDLE_MANIFEST"
    printf '  "public_base_url": "%s"\n' "$public_base_url"
    printf '}\n'
  } >"$receipt_path"
  log "wrote deployment receipt $receipt_path"
}

DEPLOY_BRANCH="${THREAD_SEARCH_DEPLOY_BRANCH:-master}"
DEPLOY_REMOTE="${THREAD_SEARCH_DEPLOY_REMOTE:-origin}"
THREAD_SEARCH_DEPLOY_ARTIFACT_DIR="${THREAD_SEARCH_DEPLOY_ARTIFACT_DIR:-dist/thread-search-public}"
THREAD_SEARCH_DEPLOY_BUNDLE_DIR="${THREAD_SEARCH_DEPLOY_BUNDLE_DIR:-dist/deploy-bundles}"
THREAD_SEARCH_DEPLOY_BUNDLE_MANIFEST="${THREAD_SEARCH_DEPLOY_BUNDLE_MANIFEST:-$THREAD_SEARCH_DEPLOY_BUNDLE_DIR/deploy-bundle-manifest.json}"
FLY_WAIT_TIMEOUT="${FLY_WAIT_TIMEOUT:-10m0s}"

require_command git

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

FLY_BIN="$(resolve_flyctl)"

require_master_head

PYTHON_BIN="$(resolve_python)"

DEPLOY_SHA="$(git rev-parse HEAD)"
DEPLOY_SHA_SHORT="$(git rev-parse --short=12 HEAD)"

for artifact_file in thread-search.sqlite manifest.json README.deploy.txt; do
  [[ -f "$THREAD_SEARCH_DEPLOY_ARTIFACT_DIR/$artifact_file" ]] || \
    fail "missing deployment artifact: $THREAD_SEARCH_DEPLOY_ARTIFACT_DIR/$artifact_file"
done

log "installing package and dev dependencies"
"$PYTHON_BIN" -m pip install -e '.[dev]'

log "running public-safe test suite"
"$PYTHON_BIN" -m pytest -q

log "refreshing deployment bundles from $THREAD_SEARCH_DEPLOY_ARTIFACT_DIR"
"$PYTHON_BIN" -m planquest.cli deploy-bundle \
  --artifact-dir "$THREAD_SEARCH_DEPLOY_ARTIFACT_DIR" \
  --out-dir "$THREAD_SEARCH_DEPLOY_BUNDLE_DIR"

log "verifying deployment bundles"
"$PYTHON_BIN" -m planquest.cli deploy-bundle-check \
  --manifest "$THREAD_SEARCH_DEPLOY_BUNDLE_MANIFEST"

deploy_args=(
  deploy
  --remote-only
  --config fly.toml
  --image-label "master-${DEPLOY_SHA_SHORT}"
  --wait-timeout "$FLY_WAIT_TIMEOUT"
  --yes
)
if [[ -n "${FLY_APP:-}" ]]; then
  deploy_args+=(--app "$FLY_APP")
fi

log "deploying $DEPLOY_BRANCH@$DEPLOY_SHA_SHORT to Fly.io"
"$FLY_BIN" "${deploy_args[@]}" "$@"

run_public_smoke_if_configured
write_receipt
log "deployment complete for $DEPLOY_BRANCH@$DEPLOY_SHA_SHORT"
