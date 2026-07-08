# CLAUDE.md

Guidance for working in this repo.

## Completion Summary

- Once a task is done, explain in plain language what changed, how it was verified, and any
  user-facing impact.

## Evidence and Scope

- If the user asks to investigate, scout, audit, confirm, or explicitly says not to fix or change
  behavior, keep the pass read-only unless the user later authorizes edits.
- For current code, deployment, CI, or merged-state questions, answer from current repo, runtime,
  and git evidence such as the trunk branch, targeted source/tests, logs, or issue status. Do not
  use private agent memory as a source of truth.

## Parallel Worktrees

- For implementation work, each terminal/agent must work in its own clean git worktree. Do not run
  two coding agents in the same checkout.
- Before making changes, verify the checkout and branch:

  ```bash
  git rev-parse --show-toplevel
  git branch --show-current
  git status --short
  ```

- Use one descriptive branch per worktree.
- Create project worktrees outside the main checkout so the repo directory stays clean. Branch from
  the current trunk/default branch when a valid `HEAD` exists:

  ```bash
  git worktree add ../planquest-my-task -b my-task <trunk-branch>
  ```

- If the repository has no initial commit yet, create an orphan worktree instead:

  ```bash
  git worktree add --orphan -b my-task ../planquest-my-task
  ```

- Agents must only edit files inside their assigned worktree until changes have passed verification.
  Do not edit the original checkout or another agent's worktree during the worktree phase.
- Coordinate write ownership before starting. If another agent owns a file or module, do not edit it
  unless explicitly told to. Avoid parallel edits to shared contracts, generated files, or design
  docs.
- Never revert unrelated changes. Stage and commit only files belonging to the current task.
- If running local servers, use different ports per worktree or stop the other server first.

## Git

- This repo uses a trunk-based workflow.
- During development, run targeted tests that match the files or contracts changed.
- Before integrating work, run the relevant available test gate from inside the worktree. If no test
  command exists yet, say so explicitly instead of inventing one.
- After tests pass in the worktree, integrate the tested commit directly to trunk/default branch.
- Commit messages should be detailed. Use a clear subject and include a body when the change has
  user-facing impact, contract changes, testing nuance, or non-obvious reasoning.
- If a local hook or test fails, fix the staged diff instead of bypassing the failure unless the
  user explicitly authorizes a bypass.

## Commands

- Prefer repo-provided scripts and package-manager commands once they exist.
- Use `rg` instead of `grep` for searching content quickly.
- Use `fd` instead of `find` for finding files quickly.
- For docs-only changes, at minimum run a cheap structural check such as:

  ```bash
  git diff --check
  ```

## Invariants

- Keep behavior, tests, and documentation aligned. When changing a cross-file contract, update the
  relevant docs and all callers in the same change.
- Treat external input as untrusted. Validate and bound data at process, network, file, and API
  boundaries.
- Keep secrets out of the repo, logs, test output, and final summaries.

## Conventions

- Follow existing code style and local helper APIs before introducing new patterns.
- Keep changes narrowly scoped to the task. Avoid unrelated refactors and metadata churn.
- Add tests in proportion to risk and blast radius. Shared behavior and user-facing workflows need
  stronger coverage than isolated internal helpers.
- Add comments only where the code is not self-explanatory.
