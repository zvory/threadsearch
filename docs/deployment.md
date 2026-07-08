# Deployment

The deployable unit is a small Python HTTP server backed by a private SQLite FTS index.

Do not publish `data/thread-search-threadmarks.jsonl`, `data/raw/`, or a downloadable copy of `data/thread-search.sqlite` unless the author and site rules allow redistribution. The intended public shape is server-side search with short snippets and links back to Sufficient Velocity.

Runtime search, status, validation, and health checks open the SQLite index in read-only mode. Mount the exported artifact as a private read-only volume in production.

The CLI `context` command and server `--private-fulltext` mode are for local retrieval and private reading. They can emit larger text chunks or full threadmark text. Do not expose either as a public endpoint without explicit redistribution permission.

The `serve` command fails closed for common public-deployment mistakes: non-loopback hosts require both `--require-launch-ready` and `--require-artifact-manifest`, and `--private-fulltext` is refused on non-loopback hosts unless the operator passes an explicit override. The artifact manifest validation checks permission evidence and refuses runtime settings that broaden the recorded public contract. Treat override flags as private-network escape hatches, not normal deployment settings.

## Build The Corpus

Run the crawl from your machine, after reviewing `docs/access-policy.md`:

```sh
export THREAD_SEARCH_CONTACT='you@example.com'
.venv/bin/thread-search plan --manifest data/crawl-plan.json
.venv/bin/thread-search build --delay 8 --probe Soviet
```

For a stricter page-at-a-time crawl, warm the cache first:

```sh
.venv/bin/thread-search prefetch --limit 1 --delay 30
```

Repeat until `remaining_selected_uncached` is `0`, then build and validate without any network requests:

```sh
.venv/bin/thread-search build --offline --probe Soviet
```

Or let the CLI print the next safest command for the current state:

```sh
.venv/bin/thread-search next-step --offline
.venv/bin/thread-search next-step --offline --public-base-url http://127.0.0.1:8765
```

Run the printed command and rerun `next-step --offline` until it recommends the final audit. This keeps one-page network fetches, offline build, artifact export, and audit in the intended order. Pass `--public-base-url` when the final audit should include live HTTP smoke evidence.
After a passing final audit, `next-step` also checks `dist/deploy-bundles/deploy-bundle-manifest.json` by default; if it is missing or fails verification, the recommended next command is `deploy-bundle`.

For a persistent operator checklist:

```sh
.venv/bin/thread-search runbook --offline --out data/operator-runbook.md
```

Validation expects the main `Threadmarks` category to contain 269 records and only category `1`. It fails if `Sidestory` or `Apocrypha` category IDs appear.

The crawler retries transient `429`, `5xx`, and network failures with conservative waits. If Sufficient Velocity sends `Retry-After`, that value is respected.

Before launching a public source-linked search instance, run:

```sh
.venv/bin/thread-search status
.venv/bin/thread-search permission-note --out data/permission-note.md
.venv/bin/thread-search permission-request --out data/permission-request.md --public-base-url https://your-public-host.example --operator "Your handle"
.venv/bin/thread-search permission-note --check --out data/permission-note.md
.venv/bin/thread-search launch-check --probe Soviet --probe Cuba
.venv/bin/thread-search audit --probe Soviet --probe Cuba
```

The permission note is a local deployment record. The request draft is a convenience artifact for asking the author or site contact; it is not approval evidence by itself. After you receive a reply, fill the note in with the author permission, site-rule review, public deployment scope, and operator decision. Keep the named checklist items from the template; the check fails while required sections or checklist items are missing, TODO placeholders remain, any box is unchecked, any checklist detail is blank or generic, date fields lack `YYYY-MM-DD`, or the operator decision is negative or too vague to confirm public source-linked search deployment.

For a deployment artifact where only `data/thread-search.sqlite` is present:

```sh
.venv/bin/thread-search launch-check --db-only --probe Soviet --probe Cuba
```

The launch check fails unless the full expected corpus is indexed, probes return results, excluded categories are absent, and `--private-fulltext` is not selected.

## Export Artifact

After `launch-check` passes, create the private backend artifact:

```sh
.venv/bin/thread-search artifact --probe Soviet --probe Cuba --permission-note data/permission-note.md --public-contact "$THREAD_SEARCH_PUBLIC_CONTACT" --removal-request-url "$THREAD_SEARCH_REMOVAL_REQUEST_URL"
```

If the permission note is incomplete, the artifact command fails before writing `dist/` and prints the remaining placeholders and unchecked checklist items. It also refuses to export unless `--public-contact` and `--removal-request-url` are set to non-placeholder mailto/email/HTTP(S) values, because public deployments need a visible operator/removal path. Complete those items and rerun `permission-note --check` before exporting.

This writes `dist/thread-search-public/` with:

- `thread-search.sqlite`: the private server-side search database
- `manifest.json`: checksum, index counts, validation checks, public server caps, the public endpoint contract, and the runtime contract
- `README.deploy.txt`: deployment warning and run command

The artifact command validates the SQLite database in `--db-only` mode before copying it. With the default settings it refuses to export unless the index has 269 main threadmarks, has no excluded categories, returns a result for the readiness probe, and the permission note is complete. The manifest includes the permission-note path, byte count, SHA-256 hash, the search-only public API endpoint contract, the always-on word-variant search contract, the no-public-full-text contract, and the runtime contract, not the note body.

Manifest-gated serving requires the manifest to sit next to `thread-search.sqlite`. When `serve --require-artifact-manifest` is used, the CLI rejects `--private-fulltext` and rejects public cap values above the manifest defaults. Lower caps are allowed.

Treat `dist/thread-search-public/thread-search.sqlite` as private backend data. It contains full indexed thread text for server-side snippet generation, so it must be mounted into the service and kept away from static hosting, public object storage, and direct download URLs unless explicit redistribution permission covers that. Final artifact audit also fails if the artifact directory contains files outside the expected backend set, such as extracted JSONL, raw HTML, notes, or other static assets.

After exporting, include the manifest in the final audit:

```sh
.venv/bin/thread-search audit --probe Soviet --probe Cuba --artifact-manifest dist/thread-search-public/manifest.json --permission-note data/permission-note.md
.venv/bin/thread-search audit --probe Soviet --probe Cuba --artifact-manifest dist/thread-search-public/manifest.json --permission-note data/permission-note.md --public-base-url http://127.0.0.1:8765
```

The audit report is the final evidence checklist. It fails on incomplete corpus size, wrong categories, missing SQLite index, failed probe searches, failed public launch checks, incomplete permission evidence, a missing or checksum-mismatched artifact database, unexpected files in the artifact directory, an artifact manifest that does not match the private-backend deployment contract, or failed live HTTP smoke checks when `--public-base-url` is provided.

## Deployment Bundles

After the artifact audit passes, create upload bundles:

```sh
.venv/bin/thread-search deploy-bundle
.venv/bin/thread-search deploy-bundle-check
```

This writes `dist/deploy-bundles/` with:

- `thread-search-app.tar.gz`: public-safe app source, docs, tests, Docker/Compose, nginx, and systemd examples. It excludes `data/`, `dist/`, `.venv/`, and `.git/`.
- `thread-search-private-artifact.tar.gz`: private backend artifact containing only `thread-search.sqlite`, `manifest.json`, and `README.deploy.txt`. This bundle contains the server-side full-text SQLite index; do not publish it or put it in a web root.
- `deploy-bundle-manifest.json`: checksums, file lists, and safety metadata for the two tarballs.

The command validates `dist/thread-search-public/manifest.json` before writing the private artifact bundle, and fails if the artifact directory contains unexpected files such as raw HTML or extracted JSONL.
The check command verifies the bundle manifest, tarball checksums and sizes, public app tarball file list, absence of private top-level paths in the app tarball, and that the private artifact tarball contains exactly the allowed backend files.

The app bundle includes `.github/workflows/ci.yml`, which runs the public-safe test suite without `data/` or `dist/`. CI verifies code and deployment guardrails, not the private production artifact; run `deploy-bundle-check` and the live audit on the machine that has the private artifact files.

## Deploy Every Master Commit

The production Docker image uses `dist/thread-search-public/`, which contains the private server-side SQLite artifact and is intentionally ignored by git. Because CI does not have that artifact by default, the reliable deployment path for this repository is a local deploy from the checkout that owns the private artifact.

From a clean deployment checkout:

```sh
git checkout master
THREAD_SEARCH_PUBLIC_BASE_URL=https://threadsearch.fly.dev deploy/master-deploy.sh
```

The wrapper refuses to deploy unless the checkout is on `master`, the working tree is clean, and the local `master` can be fast-forwarded to exactly `origin/master`. It then creates or reuses `.venv`, installs the package with dev dependencies, runs `pytest -q`, requires `dist/thread-search-public/thread-search.sqlite`, `manifest.json`, and `README.deploy.txt`, refreshes the upload bundles, runs `deploy-bundle-check`, deploys with `flyctl deploy --remote-only`, and writes a local receipt under `data/deployments/`.

Set `THREAD_SEARCH_PUBLIC_BASE_URL` to run the live `public-smoke` check after Fly reports a successful deploy. Leave it unset only when the target URL is not yet reachable. You can pass additional Fly deploy flags after the script name, for example:

```sh
THREAD_SEARCH_PUBLIC_BASE_URL=https://threadsearch.fly.dev deploy/master-deploy.sh --strategy rolling
```

The script assumes Fly credentials are already available through `flyctl auth login` or `FLY_API_TOKEN`. If CI deployment becomes required later, first move the private artifact into a protected runtime storage path or another secure artifact source; do not commit `data/` or the private SQLite artifact to make CI builds work.

## Run Locally

```sh
.venv/bin/thread-search serve --host 127.0.0.1 --port 8765
```

Health and stats endpoints:

- `GET /healthz`
- `GET /robots.txt`
- `GET /api/stats`
- `GET /api/threadmarks`
- `GET /api/search?q=Cuba`
- `GET /api/search?q=Cuba&mode=any`
- `GET /api/search?q=Cuba&from=10&to=25`

`/healthz` validates that the SQLite index is readable and contains indexed threadmarks/chunks. It returns `503` when the database is missing, corrupt, or not ready. The Docker image uses this endpoint for its container healthcheck.

Public search returns source-linked hits grouped by the threadmark where each hit appears. Threadmark groups are ordered chronologically from oldest to newest, and every hit in each matching threadmark is returned. Word variants are always enabled, so searches such as `Cuba` can surface `Cuban` without a separate UI/API toggle. Public search accepts only the query text, all-words/any-words mode, and optional `from`/`to` threadmark-order filters. `/api/stats` includes the source reader URL, source host, public access mode, and thread picker options; the web UI exposes a fuzzy thread picker seeded from the current Sufficient Velocity reader.

Public API caps are enforced server-side:

- Threadmark metadata rows default to at most `300`.
- Query strings default to at most `120` normalized characters.
- Public API routes default to at most `60` requests per client IP per minute.

Tune these with `--public-search-limit`, `--public-threadmark-limit`, `--max-query-chars`, and `--public-rate-limit-per-minute`.

For non-loopback serving and exported public artifacts, the CLI and artifact exporter reject disabled or unusually high public caps by default:

- Search limit: `1` to `100`
- Threadmark metadata limit: `1` to `500`
- Query length: `1` to `240` characters
- Public API rate limit: `1` to `600` requests per client IP per minute

Use `--allow-unsafe-public-caps` only for a deliberate private-network or separately rate-limited deployment decision.

For an internet-facing deployment, keep a reverse proxy or host-level rate limit in front of the Python server as well. The built-in limiter is a single-process backstop, not a substitute for platform abuse controls.

The server also returns `X-Robots-Tag: noindex, nofollow`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, a nonce-based `Content-Security-Policy`, and a disallow-all `robots.txt`. Keep those behaviors in front of any public deployment unless the permission and site-rule review explicitly allow indexing.

For public deployments, configure a visible operator/removal path with `--public-contact` or `THREAD_SEARCH_PUBLIC_CONTACT`, and `--removal-request-url` or `THREAD_SEARCH_REMOVAL_REQUEST_URL`. These values are exposed through `/api/stats` and the page notice; use a public email address, `mailto:` link, or HTTPS form URL. Empty values and reserved example placeholders are rejected for public/non-loopback serving, artifact export, public smoke checks, and artifact audit.

For a public process, start the server with the launch gate enabled:

```sh
.venv/bin/thread-search serve \
  --db dist/thread-search-public/thread-search.sqlite \
  --host 0.0.0.0 \
  --port 8765 \
  --require-launch-ready \
  --require-artifact-manifest \
  --artifact-manifest dist/thread-search-public/manifest.json \
  --public-contact "$THREAD_SEARCH_PUBLIC_CONTACT" \
  --removal-request-url "$THREAD_SEARCH_REMOVAL_REQUEST_URL" \
  --probe Soviet \
  --probe Cuba
```

After the process is reachable, smoke-test the live HTTP surface:

```sh
.venv/bin/thread-search public-smoke --base-url http://127.0.0.1:8765 --require-artifact-manifest --probe Soviet --probe Cuba
```

For author review, generate a no-story-text packet with the live prototype URL, safety scope, verification hashes, and demo links:

```sh
.venv/bin/thread-search author-review --offline --public-base-url http://127.0.0.1:8765 --artifact-manifest dist/thread-search-public/manifest.json --permission-note data/permission-note.md --deploy-bundle-manifest dist/deploy-bundles/deploy-bundle-manifest.json --out data/author-review.md
```

This verifies the running server still has `noindex`/`nofollow` headers, disallow-all `robots.txt`, and the simplified search UI shell; reports public source-linked search mode, contact/removal metadata, and the validated-manifest startup signal through `/api/stats`; keeps the private full-text threadmark API unavailable; blocks common private corpus/artifact download paths; and returns grouped source-linked results for the launch probes with word variants enabled. For local loopback development before artifact export, omit `--require-artifact-manifest`.
The live audit runs its own public smoke pass. With the default `60` requests/minute per-IP limiter, wait a minute between a standalone `public-smoke` run and a live `audit`, or restart the local loopback process before the audit.

## Ephemeral Author Preview

For a short-lived author review link from a local machine, use the preview helper. It starts the same manifest-gated loopback server as the production command, then optionally opens a `localtunnel` URL through `npx`. The SQLite artifact still stays server-side and private; the public URL only reaches the source-linked search app.

```sh
THREAD_SEARCH_PUBLIC_CONTACT="mailto:contact@your-domain.tld" \
THREAD_SEARCH_REMOVAL_REQUEST_URL="https://your-domain.tld/thread-search-removal" \
.venv/bin/thread-search preview-start --probe Soviet --probe Cuba
```

If the loopback server is already running with the guarded settings, reuse it and start only the tunnel:

```sh
THREAD_SEARCH_PUBLIC_CONTACT="mailto:contact@your-domain.tld" \
THREAD_SEARCH_REMOVAL_REQUEST_URL="https://your-domain.tld/thread-search-removal" \
.venv/bin/thread-search preview-start --skip-server --probe Soviet --probe Cuba
```

The helper records process IDs and the public URL in `data/public-preview-state.json`. Check the live preview and run the same public smoke checks with:

```sh
.venv/bin/thread-search preview-status --smoke --probe Soviet --probe Cuba
```

Then write durable evidence and regenerate the author packet against the public preview URL:

```sh
.venv/bin/thread-search audit --probe Soviet --probe Cuba --artifact-manifest dist/thread-search-public/manifest.json --permission-note data/permission-note.md --public-base-url "$PUBLIC_PREVIEW_URL" --json --out data/public-preview-audit.json
.venv/bin/thread-search author-review --offline --public-base-url "$PUBLIC_PREVIEW_URL" --artifact-manifest dist/thread-search-public/manifest.json --permission-note data/permission-note.md --deploy-bundle-manifest dist/deploy-bundles/deploy-bundle-manifest.json --probe Soviet --probe Cuba --out data/author-review.md
```

Stop a recorded preview when the review window closes:

```sh
.venv/bin/thread-search preview-stop
```

With `--require-launch-ready`, the server refuses to bind unless the SQLite index has the expected 269 main threadmarks, excluded categories are absent, probe searches work, `--private-fulltext` is not enabled, and non-placeholder public contact/removal metadata is configured for non-loopback hosts. With `--require-artifact-manifest`, the server also refuses to bind unless `manifest.json` validates the adjacent artifact database, public caps, contact/removal metadata, content-handling contract, runtime contract, and permission-note evidence, then exposes `artifact_manifest_validated: true` through `/api/stats` for live smoke/audit checks. Without `--require-launch-ready` or `--require-artifact-manifest`, the server refuses non-loopback hosts unless the corresponding `--allow-unguarded-public-bind` or `--allow-unmanifested-public-bind` override is passed.

For private local use:

```sh
.venv/bin/thread-search serve --host 127.0.0.1 --port 8765 --private-fulltext
```

This enables:

- `GET /threadmark/{post_id}`
- `GET /api/threadmark/{post_id}`

Do not use `--private-fulltext` for a public instance unless the permission you have explicitly covers serving full text. The CLI blocks this combination on non-loopback hosts unless `--allow-public-fulltext` is also supplied.

## Docker

The image intentionally excludes `data/` and `dist/`. Mount the built database at runtime.

The checked-in Compose file is the safer default: it builds the image, mounts the exported artifact read-only, requires `/data/manifest.json` at startup, runs the container filesystem read-only, and binds only to `127.0.0.1` so a reverse proxy can handle public HTTPS and host-level rate limiting.
It also fails during Compose interpolation unless `THREAD_SEARCH_PUBLIC_CONTACT` and `THREAD_SEARCH_REMOVAL_REQUEST_URL` are set, so the public smoke check can verify visible operator/removal metadata.

```sh
THREAD_SEARCH_PUBLIC_CONTACT="mailto:contact@your-domain.tld" \
THREAD_SEARCH_REMOVAL_REQUEST_URL="https://your-domain.tld/thread-search-removal" \
docker compose up --build
```

Equivalent direct Docker run:

```sh
docker build -t thread-search .
docker run --rm -p 127.0.0.1:8765:8765 \
  -e THREAD_SEARCH_PUBLIC_CONTACT="mailto:contact@your-domain.tld" \
  -e THREAD_SEARCH_REMOVAL_REQUEST_URL="https://your-domain.tld/thread-search-removal" \
  -v "$PWD/dist/thread-search-public:/data:ro" \
  thread-search
```

For a public deployment, put the service behind HTTPS, keep the database on a private volume, retain the page-level `noindex` header/meta behavior, and disable generic file serving from the data volume. If you use Compose or direct Docker on a public host, keep the `127.0.0.1:8765:8765` bind and route traffic through the reverse proxy instead of publishing the container directly.

## Systemd VPS

For a non-Docker VPS, use the example service under `deploy/systemd/`. It follows the same production shape as Compose: app bound only to loopback, artifact database mounted privately, manifest validation required at startup, public contact/removal metadata required, and nginx as the internet-facing HTTPS process.

Layout:

- `/srv/thread-search/app`: app checkout with `.venv`
- `/srv/thread-search/artifact`: private copy of `thread-search.sqlite` and `manifest.json`
- `/etc/thread-search/thread-search.env`: real public contact and removal-request values
- `/etc/systemd/system/thread-search.service`: copied from `deploy/systemd/thread-search.service.example`

Install sketch:

```sh
sudo install -d -o threadsearch -g threadsearch /srv/thread-search/app /srv/thread-search/artifact
sudo install -d -m 0750 /etc/thread-search
sudo install -m 0640 deploy/systemd/thread-search.env.example /etc/thread-search/thread-search.env
sudo install -m 0644 deploy/systemd/thread-search.service.example /etc/systemd/system/thread-search.service
sudo systemctl daemon-reload
sudo systemctl enable --now thread-search.service
```

Before enabling the service, edit `/etc/thread-search/thread-search.env` and replace the placeholder values. Copy only `dist/thread-search-public/thread-search.sqlite` and `dist/thread-search-public/manifest.json` into `/srv/thread-search/artifact`; do not copy `data/`, raw HTML, extracted JSONL, or the artifact directory into a public web root.

After DNS and HTTPS are live through nginx, run both public checks:

```sh
.venv/bin/thread-search public-smoke --base-url https://your-domain.tld --require-artifact-manifest --probe Soviet --probe Cuba
.venv/bin/thread-search audit --probe Soviet --probe Cuba --artifact-manifest dist/thread-search-public/manifest.json --permission-note data/permission-note.md --public-base-url https://your-domain.tld --json --out data/production-audit.json
```

An nginx starter config is available at `deploy/nginx-thread-search.conf.example`. It keeps nginx as the only internet-facing process, proxies to `127.0.0.1:8765`, applies a host-level `/api/` rate limit, repeats noindex/security headers, and denies obvious private artifact paths before proxying.
