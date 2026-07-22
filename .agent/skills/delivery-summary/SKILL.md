---
name: delivery-summary
description: >
  Produce the final evidence-based engineering report. Use after implementation,
  checks, and review to summarize changed behavior, verification, important decisions,
  limitations, and residual risks without emitting a verbose action diary.
---

# Purpose

Give the user a compact, trustworthy handoff.

# Inputs

- requirements contract and completion gate;
- changed files and behavioral delta;
- exact commands and results;
- review findings and residual risks.

# Workflow

1. State the user-visible result.
2. List only material changes.
3. Report exact checks executed and their status.
4. Explain one or more non-obvious decisions when relevant.
5. Disclose skipped checks, environment limitations, unresolved assumptions, and residual risks.
6. Mention created branch, commit, PR, artifact, or migration status when applicable.
7. Avoid internal chain-of-thought and low-level tool chronology.

# Decision rules

- Never say “all tests pass” when only targeted tests ran.
- Distinguish implemented, verified, reviewed, and merely recommended work.
- Do not bury a failed check under a success summary.
- Do not include unrelated future enhancements unless they block safe use.

# Output

Use `templates/final-summary.md` or an equivalent concise structure.

# Stop conditions

Stop after the result, verification evidence, significant decisions, and residual risk are clear.
