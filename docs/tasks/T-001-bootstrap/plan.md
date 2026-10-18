# T-001: repo bootstrap

## Goal

A minimal, checked Python skeleton and the working rules, so later tasks (P0 in design.md §11)
start from a green baseline.

## Scope

- `AGENTS.md`: project rule at the top, task-directory convention, workflow, how to run checks.
- `pyproject.toml` + `uv.lock`: package `piotr_voice_hub` under `src/`, Python 3.12, no
  runtime dependencies. Dev group: ruff, pyright, pytest.
- One trivial real test (package imports, installed version).
- `Makefile` with `make check`: ruff check, ruff format --check, pyright, pytest via `uv run --locked`.
- `.github/workflows/ci.yml`: `make check` on pull requests and pushes to `main`, ubuntu-latest, no GPU.
- `README.md` (purpose + pointer to design), `.gitignore`.

## Out of scope

- Any audio, satellite, STT, TTS or API code. Nothing is copied from `piotr_the_ai_server` yet.
- Docker, compose, firmware.
- GPU or ML dependencies.

## Decisions

- Python 3.12: design.md does not pin a version; the planned base image
  (`nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04`) ships Python 3.12.
- Build backend `uv_build`; pyright in `standard` mode via `pyright[nodejs]` (bundled Node, no
  system Node needed).

## Acceptance

- `make check` passes on piotr and in CI.
- Fresh clone + `make check` works with only uv installed.
- Docs read sensibly to Maciej.
