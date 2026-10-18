# T-001 log

## 2026-10-18

- Orientation: repo had only `LICENSE`, `README.md`, `docs/design.md` (rev. 3) on `main`.
  No `docs/tasks/` yet; this task introduces the convention.
- Installed uv 0.13.1 in user space for `grok-developer` on piotr (`~/.local/bin/uv`).
- Chose Python 3.12 (matches the Ubuntu 24.04 CUDA base image in design.md §9); uv manages
  the interpreter, host Python is 3.14.
- Added skeleton, `AGENTS.md`, Makefile, CI workflow. `make check` passes locally.
- Status: PR open, awaiting independent review and CI.

## 2026-10-18 (review fixes)

- Independent review of PR #1 found CI failing: `astral-sh/setup-uv@v10` does not resolve
  (no `v10` major tag exists; latest is `v10.3.0`).
- Pinned actions by full commit SHA: `actions/checkout@3d3c42e5…` (v7.0.1, the commit the `v7`
  tag points to) and `astral-sh/setup-uv@1c37ad07…` (v10.3.0), with `version: "0.13.1"` for uv,
  matching the version used locally.
- Nits: removed `.pyright/` from `.gitignore`; the version test now reads name and version from
  `pyproject.toml` (tomllib) and compares them with the installed metadata.
- `make check` passes locally. Status: pushed, waiting for CI.
