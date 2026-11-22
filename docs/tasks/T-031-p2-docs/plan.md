# T-031: P2 docs

**Status:** planned. Branch `t-031-p2-docs`.

## Goal

Bring design.md, the tasks README, and any runbooks in line with the finished P2 firmware,
fixtures, liveness check and soak (design.md §11, "docs").

## Scope

- Update design.md §8–§11 and any API/pipeline wording that still says the follow-up ready
  cue is missing, or that cue end is only inferred from the media player.
- Point the tasks README statuses at the merged P2 tasks and close the matching
  carry-forwards.
- Short operator notes: how to build/validate firmware (not the live flash, which stays in
  T-027's checklist), where fixtures live, how to run the liveness-related logs, how to run
  the soak summary.
- Fold in leftover doc nits from T-027–T-030 reviews if they were deferred here.

## Out of scope

- Behaviour changes, flashing, or a new soak run.
- Copying `bobiverse` engineering principles into this repo.

## Approach

One docs-only PR after T-027–T-030 have landed (or the last of them is in review). Smallest
diff: edit the existing design and task docs; do not add a parallel manual.

## Tests

- No code tests. `make check` stays green. Links and SHA references in the docs resolve
  (no `$SHA` raw paths that 404).

## Manual (hardware) checklist outline

- [ ] Read-through of design.md §8–§11 against the merged firmware and hub behaviour.
- [ ] Carry-forwards that T-027–T-030 completed are marked done.
- [ ] No live device step.

## Risks

- Writing this before the firmware is flashed will document intent as if it were already
  on the devices. Gate the "reflashed" wording on the actual live session result.

## Dependencies

- T-027 … T-030.
