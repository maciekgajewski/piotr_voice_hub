# Agent instructions

## Rule 1: SIMPLICITY, NARROW SCOPE, RELIABILITY

> **SIMPLICITY, NARROW SCOPE, RELIABILITY.** The hub is a voice I/O engine and nothing more.
> Prefer the smallest mechanism that works. Do not add features, options or integrations that
> are not in [docs/design.md](docs/design.md). Every change must keep the hub running unattended
> for weeks: bounded queues, timeouts on every wait, supervised reconnects, fail loudly in logs.

This rule overrides everything below.

## Sources of truth

- [docs/design.md](docs/design.md) defines scope, architecture, API, config and plan.
  Work outside it needs a design change approved by Maciej first.
- Code may be copied from `maciekgajewski/piotr_the_ai_server` @ `a299643` as listed in
  design.md §5. Never modify that repository.

## Task directories

Every task lives in `docs/tasks/T-NNN-slug/` (for example `docs/tasks/T-001-bootstrap/`):

- `plan.md`: goal, scope, out of scope, steps, acceptance criteria. Small and focused.
- `log.md`: **append-only** log with dated entries (`## YYYY-MM-DD`). Record decisions,
  findings, review results and status changes. Never rewrite earlier entries.
- Any other working files the task needs (measurements, notes, scratch scripts).

Task numbers are sequential and never reused.

## Workflow

1. One task = one branch (`t-NNN-slug`) + one PR, with tests.
2. Write `plan.md` before code; keep `log.md` updated as you go.
3. Run `make check` locally before pushing.
4. Every PR is reviewed by a fresh, independent, one-off reviewer agent that has not worked
   on the task. Fix all findings and re-review until clean. Record the outcome in `log.md`.
5. Merge only after the review is clean and CI is green.
6. Every PR description includes a short **manual test checklist** that Maciej can run on the
   hardware for technical acceptance.
7. Maciej reviews design and behaviour, not code. Design changes go into `docs/design.md`
   in their own PR, or clearly called out in the task PR.

## Checks

Requires [uv](https://docs.astral.sh/uv/). From the repo root:

```sh
make check         # ruff check, ruff format --check, pyright, pytest
make format        # auto-format and apply safe lint fixes
uv run pytest -k x # run a subset of tests
```

CI (`.github/workflows/ci.yml`) runs `make check` on every pull request and on pushes to `main`.

## Machine

- Do not run `sudo`. If a task needs it, ask Maciej to run the exact command.
- The GPU is shared with Ollama, which is not part of this project. Do not stop or reconfigure it.

## Python

- Python 3.12, `src/` layout, package `piotr_voice_hub`. Dependencies are managed with uv;
  commit `uv.lock`.
- Add a dependency only when the current task needs it.
- Keep interfaces in `interfaces.py` and messages in `messages.py`, per module.
- Imports go at the top of the file; no imports inside functions.
- Keep `__init__.py` minimal, preferably empty.
- A config file is required to start the hub; do not hardcode config values in code (test
  tools excepted). Command-line options override config values.
- For internal JSON-like dicts, omit keys whose value is `None`.

## Error handling

- When in doubt, kill the process; Docker restarts it. Use asserts for invariants.
- Catch only exceptions you know how to handle.
- Every await on I/O or another task has a timeout. Every queue is bounded.

## Logging

- Include a stable per-instance prefix when work can interleave, e.g. `Satellite[voice-pe-bedroom]`.
- Every interaction between components and every state change is logged at DEBUG with enough
  detail to debug.
- INFO is user interface: brief, the important events only (connections, accepted utterances,
  external calls, playback). Abbreviate big objects.
