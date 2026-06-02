---
name: log-decision
description: Record an architectural decision for the Listener project and propagate it across all docs. Use whenever a design choice is settled (hardware, firmware, connectivity, pipeline, tooling) so DECISIONS.md stays the single source of truth and other docs don't drift or duplicate. Trigger on phrases like "let's go with", "decision:", "we'll use X instead of Y", or after resolving an open question.
---

# Log a decision

Keep `docs/DECISIONS.md` as the single source of truth and stop documentation from
duplicating or going stale. Do these steps in order.

## Steps
1. **Read** `docs/DECISIONS.md`. Find the highest existing `ADR-0NN` number.
2. **Append a new ADR** at the TOP of the `## Decisions` list (newest first):
   ```
   ### ADR-0NN — <short title>
   **<today's date, YYYY-MM-DD>.** <1–3 sentences: the choice + the rationale +
   what was rejected and why.> <If it reverses a prior ADR, say "Supersedes ADR-0MM".>
   ```
   Use today's date from the session context. Increment NN by 1.
3. **Close the Open Question** this decision answers: remove it from the
   `## Open Questions` section (or mark it resolved with a pointer to the ADR).
   If the decision raises NEW questions, add them to Open Questions.
4. **Propagate.** Grep the repo for any doc that stated the OLD choice or restates
   this decision's rationale. Update those to match, and replace duplicated
   rationale with a link: `(see ADR-0NN)`. Check at least:
   `docs/ARCHITECTURE.md`, the relevant `docs/<area>/*.md`, `CLAUDE.md` hard
   constraints, `docs/hardware/BOM.md` / `PINOUT.md` if hardware-related.
5. **Sync memory** if the decision is durable: update the project memory file.
6. **Commit** with message `decision: ADR-0NN <title>` and push to origin/main.
   List the propagated files in the commit body.

## Guardrails
- Never restate rationale in two places — link to the ADR instead.
- If the decision contradicts a hard constraint in `CLAUDE.md`, stop and flag it
  to the user before writing.
- One ADR per decision. If several were settled at once, write several ADRs.
