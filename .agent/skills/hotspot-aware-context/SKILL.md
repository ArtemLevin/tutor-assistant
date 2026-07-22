---
name: hotspot-aware-context
description: >
  Use before implementation context is opened to rank bounded quality hotspots against task relevance and available Graphify evidence.
---

# Purpose

Select the smallest explainable set of quality-relevant files and symbols without allowing global code-health scores to broaden task scope.

# Inputs

- current engineering task;
- latest bounded quality snapshot;
- available Graphify evidence;
- context size and candidate-count limits;
- source files required for deterministic line resolution.

# Workflow

1. Load the latest bounded quality snapshot.
2. Score task relevance before quality severity.
3. Use Graphify evidence as structural support, not runtime proof.
4. Resolve Python symbol line ranges deterministically.
5. Emit bounded candidates, component scores, reasons, warnings, and artifact paths.

# Decision rules

- Task relevance is the dominant ranking factor.
- An unrelated severe hotspot must not enter context only because it has a high quality score.
- Missing Graphify evidence is explicit and produces a zero graph component.
- Read source and tests before treating a hotspot as a concrete defect.
- Keep candidate count and content size bounded by context configuration.

# Output

Return a ranked candidate list with task, graph, quality and total scores, exact line ranges when available, compact reasons, warnings and the generated context artifact path.

# Stop conditions

Stop when the bounded ranked context is generated, every missing input is explicit, and no additional repository content is required merely to improve a global quality score.
