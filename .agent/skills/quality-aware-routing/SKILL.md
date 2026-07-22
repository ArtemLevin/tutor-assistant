---
name: quality-aware-routing
description: >
  Use before implementation to refine task mode, selected skills, approval needs, and verification depth from task-scoped quality evidence.
---

# Purpose

Escalate engineering controls only when bounded quality evidence is related to the requested task, while preserving every existing security and domain-risk rule.

# Inputs

- base deterministic triage;
- task-scoped hotspot context;
- quality snapshot metrics;
- Graphify evidence;
- quality routing thresholds;
- configured and discovered verification commands.

# Workflow

1. Preserve the base triage as the minimum risk level.
2. Evaluate only task-scoped quality candidates.
3. Apply threshold rules for complexity, RP, OP, fan-in, fan-out, and centrality.
4. Add skills, requirements, mode escalation, and approval flags with evidence.
5. Build `verification-plan.json` before implementation begins.
6. Persist the route and expose it in task and completion artifacts.

# Decision rules

- Quality evidence can escalate risk but never reduce the base mode.
- Project-wide poor health alone cannot trigger deep mode.
- Complexity above the characterization threshold requires a behavior-preserving test before structural rewrite.
- Combined high RP and OP in task scope is a crisis route.
- Missing quality evidence preserves existing triage and creates an uncertainty warning.
- Every selected verification command must have a reason and source evidence.

# Output

Return the original and effective mode, approval decision, selected skills, requirements, triggered rules, warnings, scoped evidence, and a reasoned verification plan.

# Stop conditions

Stop when the route is deterministic, no base safety rule was weakened, every selected check is explained, and preimplementation test requirements are visible.
