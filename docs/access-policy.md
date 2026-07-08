# Access Policy

This project is designed to keep the crawl small, transparent, and reversible.

## Current Site Signals

Checked on July 8, 2026:

- Sufficient Velocity serves `robots.txt` at `https://forums.sufficientvelocity.com/robots.txt`.
- The official help landing page links to `Terms and rules`, `Our Rules`, `Advertising & Commercial Use Policy`, and related policy pages.
- The general `User-agent: *` section allows forum thread pages and disallows account, login, search, attachment, and system paths.
- The site explicitly disallows multiple AI and crawler user agents, including `GPTBot`, `ChatGPT-User`, `Google-Extended`, `CCBot`, `anthropic-ai`, `ClaudeBot`, and others.
- The target thread reader page returned HTTP 200 and exposes main thread reader pagination.

Official policy pages reviewed for deployment gating:

- `https://forums.sufficientvelocity.com/help/`
- `https://forums.sufficientvelocity.com/threads/rules-terms-of-service-staff-list-changelog.575/`
- `https://forums.sufficientvelocity.com/threads/the-rules-and-procedures-of-sufficient-velocity.40100/`
- `https://forums.sufficientvelocity.com/help/commerce/`

The implementation checks `robots.txt` before every network fetch. If Sufficient Velocity changes `robots.txt` to disallow the reader path for this tool's user agent, the scraper stops.

Before filling the site-rules section of `data/permission-note.md`, create a no-story-text review snapshot:

```sh
.venv/bin/thread-search site-review --refresh --delay 30 --out data/site-policy-review.md
.venv/bin/thread-search site-review --offline --out data/site-policy-review.md
```

The first command makes only a `robots.txt` network request when the cache is refreshed. The report does not download policy-page bodies; it lists official policy URLs for manual review and records machine-readable `robots.txt` decisions for the target reader, account/login/search/attachment paths, and configured AI user-agent samples.

## Rate Limits

Defaults:

- One request at a time.
- `8` seconds between network requests from the documented full-crawl command.
- Cache-first. Re-running a command reads cached pages unless `--refresh` is passed.
- No login, account, search, or attachment endpoints.
- `prefetch --limit 1` fetches at most one uncached reader page per invocation, after any required cached planning data is available.
- `--offline` is a hard cache-only mode for commands that use the fetcher; missing cached pages fail instead of falling back to the network.

Successful network fetches append receipts to `data/raw/fetch-log.jsonl`. Each receipt records the UTC time, URL, cache path, byte count, configured user agent, delay, and retry settings. Cached reads do not add receipts. New CLI invocations read this log before making network requests and wait out the remaining configured delay for the same origin, so repeated one-page commands still honor the pacing window.

## Copyright And Redistribution

The public terms page says posters retain ownership of their content while granting Sufficient Velocity a license needed to operate the service. That license is not a general license for third parties to redistribute the work.

The raw pages, extracted text, and SQLite index are stored under `data/`, which is git-ignored. Do not commit or publish these files by default.

For public deployment, get explicit permission for redistribution from the author and confirm that the deployment respects Sufficient Velocity's site rules. A public search UI should prefer short snippets, link back to original posts, disable bulk export, keep private full-text routes off, avoid commercial/advertising behavior unless explicitly allowed, and block search-engine indexing unless permission says otherwise.

Before exporting a public backend artifact, create and complete `data/permission-note.md`:

```sh
.venv/bin/thread-search permission-note --out data/permission-note.md
.venv/bin/thread-search permission-request --out data/permission-request.md --public-base-url https://your-public-host.example --operator "Your handle"
.venv/bin/thread-search permission-note --check --out data/permission-note.md
```

The request draft contains no story text and can be used to ask for explicit approval. The note stays local and ignored by git. Artifact manifests record only metadata and a SHA-256 hash so the final audit can prove the deployment went through a deliberate permission and site-rule checkpoint without publishing the note body.

Public/non-loopback serving, artifact export, public smoke checks, and artifact audit require a non-placeholder operator contact and removal-request path. Use a real public email, `mailto:` link, or HTTPS form URL; reserved example values are treated as incomplete deployment metadata.

## AI Use

Because `robots.txt` disallows OpenAI/AI user agents, this project avoids sending thread text to hosted LLM or embedding APIs. The initial index is SQLite FTS5 keyword search. If semantic search is added later, prefer a local model and document the model, corpus handling, and redistribution implications.
