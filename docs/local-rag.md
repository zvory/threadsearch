# Local Retrieval Workflow

This project does not send thread text to hosted LLM or embedding APIs. The current retrieval layer is SQLite FTS5, which is local, fast, and easy to deploy server-side.

## Retrieve Context

After building the index:

```sh
.venv/bin/thread-search context Cuba --limit 8
```

For orientation without body text:

```sh
.venv/bin/thread-search toc
.venv/bin/thread-search toc --from-order 120 --to-order 180 --format json
```

Useful variants:

```sh
.venv/bin/thread-search search Cuba --format json
.venv/bin/thread-search search Cuba --sort timeline
.venv/bin/thread-search search Cuba --alias Castro --sort timeline
.venv/bin/thread-search explain Cuba
.venv/bin/thread-search context Cuba --format json
.venv/bin/thread-search context Cuban --from-order 120 --to-order 180
.venv/bin/thread-search context Cuba communism --mode any --max-chars 1200
```

The `search` JSON output emits source URLs, bounded snippets, total matching threadmark/chunk counts, alias diagnostics, and match diagnostics without full threadmark bodies. Common unquoted stopwords such as `the`, `of`, and `and` are ignored, while quoted phrases keep their words literal. Use timeline sort when you want the direct hits in thread order, and `--alias` when alternate names should contribute to the same capped result list. When a simple term falls back to prefix matching, the web UI offers an `Exact only` retry that quotes the term so near-misses such as `Cuba` -> `Cuban` can be checked separately. Use `explain` or `/api/explain` before broadening a sparse query; it returns exact counts, prefix counts, resolved match mode, per-term breakdowns for multi-term queries, indexed term hints, and cautions without snippets or body text. The `context` command emits source URLs and bounded chunks. That output is intended for local reading, note-taking, or a local-only RAG script.

Use `--prefix-variants` or `prefix_variants=1` on the public APIs when you deliberately want word-prefix variants included even when exact hits exist, such as `Cuba` plus `Cuban`. Responses report `match_kind: "prefix-variants"` so this broader retrieval is visible in notes and scripts. Without that flag, exact-first search still falls back to prefix only when exact matching returns no hits.

Use `suggest` when you are not sure which exact terms exist in the index:

```sh
.venv/bin/thread-search suggest Cub
.venv/bin/thread-search terms --prefix Cub
.venv/bin/thread-search terms --limit 100 --min-chunks 3
.venv/bin/thread-search explain Cubaa
```

## Topic Coverage Reports

Use `report` when you need to remember everywhere a topic appears:

```sh
.venv/bin/thread-search report Cuba
.venv/bin/thread-search report Cuba --format json
.venv/bin/thread-search report Cuba --from-order 120 --to-order 180
.venv/bin/thread-search report Cuba --sort timeline
.venv/bin/thread-search report Cuba --alias Castro --sort timeline
```

Reports aggregate matching chunks by threadmark and include source links, hit counts, and representative snippets. Use repeated `--alias` values when alternate names should contribute to the same capped report. Exact search is attempted first; when a simple-word query only matches through prefix fallback, reports include that diagnostic so a near-miss such as `Cuba` -> `Cuban` stays visible. The public web UI uses the same aggregation to show how broadly a search term appears without serving full text.

`suggest` and `/api/suggest` return indexed vocabulary metadata only. They prefer prefix matches and, when a prefix has no matches, can return bounded near-term suggestions for query terms of at least four characters with edit-distance metadata. Use those near suggestions for typo recovery before widening a search. `terms` and `/api/terms` return a metadata-only vocabulary index with chunk counts, occurrence counts, optional prefix filtering, and stopword filtering by default; use it to discover durable keywords without exposing story text. `explain` combines those diagnostics into a public-safe query-resolution report.

Use `--sort timeline` when you want recap order instead of the default coverage order.

Use `coverage` when you need the same threadmark hit map without snippets:

```sh
.venv/bin/thread-search coverage Cuba
.venv/bin/thread-search coverage Cuba --alias Castro --format json
.venv/bin/thread-search coverage Soviet --sort coverage --limit 300 --bucket-size 25
```

Coverage output is metadata-only: matching threadmark titles, source links, dates/authors, hit counts, match diagnostics, and timeline buckets. It does not include snippets, mention windows, or bodies.

Use `compare` when you want a safe overview of how two or more topics line up:

```sh
.venv/bin/thread-search compare Cuba communist
.venv/bin/thread-search compare Cuba communist Soviet --format json
```

Comparison output is metadata-only: per-topic coverage totals, first/last matching threadmarks, timeline buckets, all-topic overlap, pairwise overlap counts, and source-linked overlap titles. It does not include snippets, mention windows, or bodies. Use `claim` after `compare` when you need bounded evidence snippets for a specific claim pair.

## Mention Windows

Use `mentions` when you want every bounded source-linked window for a topic:

```sh
.venv/bin/thread-search mentions Cuba
.venv/bin/thread-search mentions '"Soviet government"' --format json
.venv/bin/thread-search mentions Cuba --from-order 120 --to-order 180 --limit 200
.venv/bin/thread-search mentions Cuba --sort timeline
.venv/bin/thread-search mentions Cuba --alias Castro --sort timeline
```

Mention windows are smaller than retrieval chunks and are intended for concordance-style review. Use repeated `--alias` values to merge bounded windows for alternate names into one source-linked timeline. The public API caps both the number of returned windows and the window size.

Current corpus note: the full main-threadmark corpus has no exact `Cuba` hit. Normal search falls back to prefix matching and can surface `Cuban`; that fallback is labeled in JSON/API responses and the web UI, and the UI's `Exact only` retry searches `"Cuba"` directly. `Castro` also appears as an exact term.

## Topic Dossiers

Use `dossier` when you want one bounded retrieval bundle for a local note, prompt, or fact-check workflow:

```sh
.venv/bin/thread-search dossier Cuba
.venv/bin/thread-search dossier Cuba --format json
.venv/bin/thread-search dossier Cuba --sort timeline --mention-limit 50
.venv/bin/thread-search dossier Cuba --alias Castro --format json
```

A dossier combines threadmark coverage and concordance windows. It is still source-linked snippet retrieval, not a generated answer, and it does not include full threadmark bodies. Use repeated `--alias` values for known alternate names, people, places, or spellings that should be reviewed as one topic bundle, and use `--prefix-variants` when word-prefix variants should be reviewed as part of that same topic. The web UI renders the same bounded dossier plus a timeline recap on the search page; its Topic aliases field accepts comma-separated terms, the Word variants checkbox sends `prefix_variants=1`, and the Recap/Report/Dossier/Evidence Pack/Mentions/Coverage/Explain JSON links preserve the current safe public evidence views for local notes or scripts, including alias-aware Report and Mentions JSON. The page URL tracks the active bounded search state, and `Copy link` copies a shareable link without adding API-only defaults. For broad topics, total coverage counts stay separate from the bounded threadmark and mention lists shown in the page, and the metadata-only coverage list can show all matching threadmark titles/source links without additional story text. Coverage term diagnostics show which alias, prefix fallback, or opt-in prefix-variant mode contributed to the hit map. Its timeline buckets are clickable range filters for drilling into one part of the quest, and `Clear range` restores the full query.

Use `evidence-pack` when you want a single local artifact that combines the dossier with one or more claim checks:

```sh
.venv/bin/thread-search evidence-pack Cuba --claim communist --out data/cuba-evidence-pack.md
.venv/bin/thread-search evidence-pack Cuba --alias Castro --claim communist --format json
.venv/bin/thread-search evidence-pack "did Cuba turn communist" --format json
```

This is designed for local notes or local-only RAG prompts. It is bounded retrieval evidence with source links, not a generated answer, and it does not include full threadmark bodies. When no explicit `--claim` is supplied, question-style or possessive q-only forms such as `did Cuba turn communist` and `Cuba's communist` are split into topic and claim; plain multiword topics remain topic searches. The public `/api/evidence-pack` endpoint returns the same shape under the server's aggregate snippet budget.

Use `recap` when you want a compact timeline-oriented evidence view for reading or sharing a public-safe JSON payload:

```sh
.venv/bin/thread-search recap Cuba --claim communist
.venv/bin/thread-search recap Cuba --alias Castro --claim communist --format json
.venv/bin/thread-search recap "did Cuba turn communist" --format json
```

`recap` reuses the dossier and claim-check retrieval but emphasizes timeline snippets. It is extractive only: no generated answer, no full threadmark bodies, and source links remain the authority. Like evidence packs, recap accepts q-only question-style or possessive claim forms when no explicit `--claim` is supplied. The public `/api/recap` endpoint returns the same bounded shape under the server's aggregate snippet budget.

## Claim Checks

Use `claim` when you need a source-linked evidence label for a specific claim pair:

```sh
.venv/bin/thread-search claim Cuba communist
.venv/bin/thread-search claim Cuba communist --alias Castro
.venv/bin/thread-search claim Cuba recognition --format json
.venv/bin/thread-search claim "did Cuba turn communist" --format json
```

`claim` labels the pair as strong same-chunk overlap, adjacent-chunk overlap, weak same-threadmark-only overlap, no overlap, or missing query terms. Claim output reports whether each side used exact matching or prefix fallback, exact primary-query counts, topic-side alias diagnostics, how many bounded evidence snippets are shown versus the total overlap count, proximity/chunk-distance notes for each evidence row, whether returned evidence snippets contain lexical negation cues near highlighted claim terms, and compact caution codes for prefix-expanded, prefix-only topic, weak-proximity, missing-side, no-overlap, or negated evidence. A `topic-exact-missing` caution means the requested topic had no exact indexed hits and the evidence came from prefix variants such as `Cuba` -> `Cuban`. The CLI and public API can infer a simple claim-pair split from q-only forms and report `claim_inferred_from_query`. Treat this as retrieval evidence only: it helps decide which source links to inspect, and it should not be read as a semantic or legal conclusion.

The web UI remains a direct search surface and does not open claim checks from search terms. Use the public `/api/claim` endpoint or `claim` CLI for bounded claim evidence. The Evidence Pack and Recap APIs and CLI commands accept q-only question-style or possessive claim forms when no explicit claim is supplied, while plain multiword topic searches remain topic searches.

For private browser-based reading, run:

```sh
.venv/bin/thread-search serve --private-fulltext
```

Search results will include local full-text links in addition to the original Sufficient Velocity source links.

## Adding Semantic Search Later

If semantic search is added, keep these constraints:

- Use a local embedding model unless the author and site rules explicitly allow sending the corpus to a hosted provider.
- Store vectors in ignored `data/` files, not in git.
- Keep public web responses snippet-sized and source-linked.
- Validate the semantic index against the same category exclusion rules as `thread-search validate`.
- Keep exact keyword and term-suggestion search available even if semantic search is added.

SQLite FTS should remain the baseline search path even if vectors are added, because exact keyword lookup is important for names, countries, dates, and acronyms.
