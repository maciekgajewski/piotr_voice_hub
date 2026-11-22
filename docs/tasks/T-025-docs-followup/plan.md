# T-025: docs follow-up (session results, E5 beep, T-024 nits, T-023 runbook)

## Goal

Land the leftover documentation and test data from the November hardware sessions
and from the T-024 and T-023 reviews. No hub code change.

## Scope

- Add the 2026-11-07 and 2026-11-22 hardware results, and the 2026-11-16 checklist,
  under this task directory. Amend F-2 as closed (heard clearly later, not reproduced)
  and record the process lesson. F-1 (unplayable JFK FLAC) stays an open finding;
  its cause is under investigation in its own task.
- Add a pinned ~0.3 s beep in `tests/data` and point the checklist's E5 step at it.
  Leave E1 and E6 on the existing CLIP11 URL.
- Fix the three T-024 documentation nits from the independent review of PR #35.
- Fix the T-023 runbook teardown so it passes the `--satellite` and `--ws` arguments
  the client requires.

## Out of scope

- Hub, driver, or compose code.
- A FLAC test-data change, a play-URL format note in design.md, or any E1/E6 URL
  change (own task).
- Deploy, satellites, firewall, Docker images, or merging this PR.

## Acceptance

- `make check` passes.
- The E5 URL is a `raw.githubusercontent.com` path under `tests/data`, documented
  with a reproducible generator command.
- The T-024 checklist stop condition matches its expect line; step 6 repeats
  step 2's `--wait` fallback; the T-024 log records the PR #35 review without
  rewriting earlier entries.
- The T-023 teardown command includes `--satellite` and `--ws`.
