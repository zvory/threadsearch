# Thread Search

Local, polite search tooling for forum reader threads.

Pass a reader URL to crawl commands, or set `THREAD_SEARCH_READER_URL` for a default:

```sh
export THREAD_SEARCH_READER_URL='https://forums.sufficientvelocity.com/threads/attempting-to-fulfill-the-plan-mnkh-edition.73217/reader/'
```

The default workflow downloads only the main `Threadmarks` reader category. It does not fetch `Sidestory` or `Apocrypha`.

## Access Policy

As of the scout pass on July 8, 2026:

- `robots.txt` allows ordinary agents to fetch `/threads/.../reader/`.
- `robots.txt` disallows `/search/`, `/login/`, `/account/`, `/attachments/`, and similar account/system paths.
- `robots.txt` explicitly disallows several AI/OpenAI user agents, including `GPTBot` and `ChatGPT-User`.
- This tool is built for local, user-run archival search. It is not a training-data crawler and does not call OpenAI or other embedding APIs with the thread text.

Before running a full crawl, read [docs/access-policy.md](docs/access-policy.md). Keep `data/` private unless you have explicit permission to redistribute the text and any deployment complies with Sufficient Velocity's rules.

To snapshot the current machine-readable site access posture without downloading thread text, run:

```sh
.venv/bin/thread-search site-review --refresh --delay 30 --out data/site-policy-review.md
```

The report records `robots.txt` decisions for the target reader, common blocked forum paths, configured AI user-agent samples, and the official Sufficient Velocity policy URLs to review manually. Use `--offline` after the first snapshot to regenerate from the cached `robots.txt`.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Optional: set a contact string for the crawler user agent.

```sh
export THREAD_SEARCH_CONTACT='you@example.com'
```

## Scout

Scouting performs one cached request to the reader page and reports the category/page structure.

```sh
.venv/bin/thread-search scout
```

## Scrape Main Threadmarks

The default delay is intentionally slow and every page is cached. A full main-threadmark scrape is 27 reader pages at the time of scouting.

Plan the crawl first:

```sh
.venv/bin/thread-search plan --manifest data/crawl-plan.json
```

For the most cautious crawl, prefetch one uncached reader page per run:

```sh
.venv/bin/thread-search prefetch --limit 1 --delay 30
```

Repeat that command until `remaining_selected_uncached` is `0`. Then build only from cached pages:

```sh
.venv/bin/thread-search build --offline --probe Soviet
```

You can also target one page explicitly:

```sh
.venv/bin/thread-search prefetch --from-page 2 --to-page 2 --limit 1 --delay 30
```

Then run the full scrape/index/validation pipeline:

```sh
.venv/bin/thread-search build --delay 8 --probe Soviet
```

Transient network failures are retried conservatively by default. Tune with `--retries` and `--retry-delay` if needed.

The lower-level scrape command is still available:

```sh
.venv/bin/thread-search scrape --delay 8
```

For a limited verification run:

```sh
.venv/bin/thread-search build --max-pages 1 --expected-threadmarks 10 --probe Soviet
```

## Build Search Index

```sh
.venv/bin/thread-search index
```

## Validate Corpus

After a full crawl and index, validate that the corpus has the expected main-threadmark shape and that a probe term works:

```sh
.venv/bin/thread-search validate --probe Soviet
```

To inspect current progress at any point:

```sh
.venv/bin/thread-search status
```

Use `status --strict` when a nonzero exit should mean “not ready yet.”

Successful network fetches are recorded in `data/raw/fetch-log.jsonl`. This is an ignored local receipt trail with timestamp, URL, cache path, byte count, user agent, delay, and retry settings.

To get the next safest command in the cautious crawl/deploy workflow:

```sh
.venv/bin/thread-search next-step --offline
.venv/bin/thread-search next-step --offline --public-base-url http://127.0.0.1:8765
```

Run the printed command, then rerun `next-step --offline`. It will move from one-page prefetch commands to offline build, artifact export, and final audit as each prerequisite becomes true. Pass `--public-base-url` when you want the final audit recommendation to include live HTTP smoke evidence.
After a passing final audit, `next-step` also checks the deployment bundle manifest and recommends `deploy-bundle` if the upload bundles are missing or no longer verify.

To write a Markdown runbook for a multi-day crawl or handoff:

```sh
.venv/bin/thread-search runbook --offline --out data/operator-runbook.md
```

To write a no-story-text author review packet with prototype links, safety scope, and verification hashes:

```sh
.venv/bin/thread-search author-review --offline --public-base-url http://127.0.0.1:8765 --artifact-manifest dist/thread-search-public/manifest.json --permission-note data/permission-note.md --deploy-bundle-manifest dist/deploy-bundles/deploy-bundle-manifest.json --out data/author-review.md
```

For a short-lived author preview URL from a local machine, start the manifest-gated loopback server plus an optional `localtunnel` URL:

```sh
THREAD_SEARCH_PUBLIC_CONTACT="mailto:contact@your-domain.tld" \
THREAD_SEARCH_REMOVAL_REQUEST_URL="https://your-domain.tld/thread-search-removal" \
.venv/bin/thread-search preview-start --probe Soviet --probe Cuba
.venv/bin/thread-search preview-status --smoke --probe Soviet --probe Cuba
```

Use `preview-stop` when the review window closes. See [docs/deployment.md](docs/deployment.md) for the full audit and author-packet refresh sequence.

## Search From CLI

```sh
.venv/bin/thread-search search Cuba
.venv/bin/thread-search search Cuba --format json
.venv/bin/thread-search search Cuba --sort timeline
```

The CLI search command is a direct SQLite FTS lookup. It supports all-words/any-words mode, optional threadmark-order range filters, grouped or per-chunk results, and relevance or timeline sorting. Exact matching is tried first; when a simple word has no exact hit, search falls back to a word-prefix FTS query such as `Cuba` -> `Cuban`.

The public web UI/API is intentionally narrower: it accepts search text, optional threadmark range, and all-words/any-words mode, always includes word variants such as `Cuba`/`Cuban`, and returns every matching hit grouped under its source threadmark in chronological order.

For a metadata-only table of contents:

```sh
.venv/bin/thread-search toc
```

Corpus note: in the complete main-threadmark corpus crawled on July 8, 2026, exact `Cuba` has no hits. Normal search falls back to prefix matching and can surface `Cuban`, with the fallback labeled in the public UI/API. Use `Soviet` as the launch-readiness probe because it is a stable exact hit.

## Local Web UI

```sh
.venv/bin/thread-search serve --port 8765
```

Then open http://127.0.0.1:8765.

For private local reading, enable full-text threadmark pages:

```sh
.venv/bin/thread-search serve --port 8765 --private-fulltext
```

Keep `--private-fulltext` off for public deployments unless redistribution permission explicitly covers full text. The CLI refuses to expose private full-text mode on a non-loopback host unless you pass an explicit override flag.

The public API has server-side caps for query length, threadmark-list size, and requests per client IP. See [docs/deployment.md](docs/deployment.md) before exposing it to the internet.
The web UI and stats endpoint include the original Sufficient Velocity reader URL plus a public-mode notice for attribution and source navigation. For a public instance, set `--public-contact` or `THREAD_SEARCH_PUBLIC_CONTACT`, and set `--removal-request-url` or `THREAD_SEARCH_REMOVAL_REQUEST_URL`, so readers and rights holders have a visible operator/removal path. Empty values and reserved example placeholders are rejected for public/non-loopback serving, artifact export, public smoke checks, and artifact audit.

For public serving, export the artifact first, then use `serve --host 0.0.0.0 --require-launch-ready --require-artifact-manifest --artifact-manifest dist/thread-search-public/manifest.json --probe Soviet --probe Cuba` so a partial, unsafe, or permission-ungated database cannot bind accidentally. Manifest-gated serving refuses runtime settings that broaden the exported public contract. Non-loopback hosts require both the launch gate and artifact manifest gate by default; use `--allow-unguarded-public-bind` or `--allow-unmanifested-public-bind` only for a deliberate private-network override.

After starting a manifest-backed public-mode server, smoke-test the live HTTP surface:

```sh
.venv/bin/thread-search public-smoke --base-url http://127.0.0.1:8765 --require-artifact-manifest --probe Soviet --probe Cuba
```

This checks the running server's noindex/robots headers, simplified search UI shell, health and stats contract, validated-manifest startup signal, visible contact/removal metadata, disabled full-text route, blocked private corpus/artifact download paths, and grouped source-linked probe searches with word variants enabled. Omit `--require-artifact-manifest` only for local loopback development before export.
The live audit runs its own public smoke pass. With the default `60` requests/minute per-IP limiter, wait a minute between a standalone `public-smoke` run and a live `audit`, or restart the local loopback process before the audit.

Before any public source-linked search deployment, run:

```sh
.venv/bin/thread-search permission-note --out data/permission-note.md
.venv/bin/thread-search permission-request --out data/permission-request.md --public-base-url https://your-public-host.example --operator "Your handle"
.venv/bin/thread-search permission-note --check --out data/permission-note.md
.venv/bin/thread-search launch-check --probe Soviet --probe Cuba
.venv/bin/thread-search audit --probe Soviet --probe Cuba
```

The first command writes a local evidence template. The second writes a no-story-text permission request draft you can send to the author or relevant site contact. After you receive a reply, edit the permission note to record the author permission, site-rule review, public deployment scope, and operator decision; keep the named checklist items so the export gate can verify that each required evidence category was addressed. The check fails while required sections or checklist items are missing, TODO placeholders remain, any box is unchecked, any checklist detail is blank or generic, date fields lack `YYYY-MM-DD`, or the operator decision is negative or too vague to confirm public source-linked search deployment.

To package the private server-side database and a manifest for deployment:

```sh
.venv/bin/thread-search artifact --probe Soviet --probe Cuba --permission-note data/permission-note.md --public-contact "$THREAD_SEARCH_PUBLIC_CONTACT" --removal-request-url "$THREAD_SEARCH_REMOVAL_REQUEST_URL"
```

This writes `dist/thread-search-public/`. Do not serve that directory as static files; the SQLite database is only meant to be mounted privately behind the search server. Export and final audit reject unexpected files in that directory, such as extracted JSONL, raw HTML, notes, or other static assets. The export fails unless `--public-contact` and `--removal-request-url` are set to non-placeholder mailto/email/HTTP(S) values. The manifest records only permission-note metadata and a hash, not the note text. It also records the public runtime contract: launch checks and manifest validation stay enabled, public full-text stays disabled, public caps may only be lowered at serve time, and the public UI supports contact/removal notice metadata.

After exporting, include the artifact manifest in the final audit:

```sh
.venv/bin/thread-search audit --probe Soviet --probe Cuba --artifact-manifest dist/thread-search-public/manifest.json --permission-note data/permission-note.md
.venv/bin/thread-search audit --probe Soviet --probe Cuba --artifact-manifest dist/thread-search-public/manifest.json --permission-note data/permission-note.md --public-base-url http://127.0.0.1:8765
```

When `audit` receives both `--artifact-manifest` and `--public-base-url`, its live smoke check requires `/api/stats` to report that the running server validated an artifact manifest at startup. For server-side deployment notes, see [docs/deployment.md](docs/deployment.md).
After a passing artifact audit, `deploy-bundle` creates separate upload tarballs for the public-safe app code and the private server-side artifact:

```sh
.venv/bin/thread-search deploy-bundle
.venv/bin/thread-search deploy-bundle-check
```

The app tarball excludes `data/` and `dist/`. The private artifact tarball contains only `thread-search.sqlite`, `manifest.json`, and `README.deploy.txt`; do not publish it or place it in a web root.
To deploy the exact current `master` commit from the machine that holds the private artifact, run:

```sh
THREAD_SEARCH_PUBLIC_BASE_URL=https://planquest-search.net deploy/master-deploy.sh
```

The deploy wrapper refuses dirty trees and non-`master` branches, fast-forwards from `origin/master`, creates or reuses `.venv`, runs the public-safe tests, refreshes and verifies deploy bundles, deploys with Fly.io, and runs live `public-smoke` when `THREAD_SEARCH_PUBLIC_BASE_URL` is set.
The included `compose.yaml` runs the exported artifact read-only, requires the artifact manifest at startup, requires `THREAD_SEARCH_PUBLIC_CONTACT` and `THREAD_SEARCH_REMOVAL_REQUEST_URL` to be set, and binds the service to `127.0.0.1:8765` for use behind a reverse proxy.
An example nginx reverse-proxy config is available at `deploy/nginx-thread-search.conf.example`; keep the app bound to loopback and expose HTTPS through the proxy.
For a non-Docker VPS, `deploy/systemd/` contains a hardened loopback-only service example and environment template that use the same manifest-gated startup command.

## Data Layout

Generated files are ignored by git:

- `data/raw/`: cached HTML and `robots.txt`
- `data/raw/fetch-log.jsonl`: local network-fetch receipt log
- `data/permission-note.md`: local public-deployment permission and site-rule review note
- `data/permission-request.md`: optional local request draft; this is not approval evidence by itself
- `data/author-review.md`: optional no-story-text prototype review packet for author/operator review
- `data/thread-search-threadmarks.jsonl`: extracted main-threadmark records
- `data/thread-search.sqlite`: local SQLite FTS index
- `dist/`: private deployment artifacts
