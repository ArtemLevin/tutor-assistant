---
name: security-review
description: >
  Review security-sensitive changes involving authentication, authorization,
  secrets, user input, file handling, SQL, shell execution, network requests,
  deserialization, dependencies, or data exposure. Use only when such a boundary is touched.
---

# Purpose

Find exploitable trust-boundary failures and unsafe defaults in the changed scope.

# Inputs

- requirements and threat-relevant context;
- actual diff and data flow;
- authentication and authorization model;
- executed security checks when available.

# Workflow

1. Identify assets, actors, trust boundaries, and attacker-controlled inputs.
2. Trace validation, normalization, authorization, storage, logging, and output encoding.
3. Check default-deny behavior and object-level authorization.
4. Review secret handling, command/query construction, path handling, and deserialization.
5. Check failure messages and logs for sensitive disclosure.
6. Review dependency or configuration changes for unsafe permissions and defaults.
7. Add only concrete findings with an exploitation or impact path.

# Decision rules

- Authentication does not imply authorization.
- Validation must occur at the trust boundary and authorization at the resource boundary.
- Reject string-built shell or SQL when structured APIs exist.
- Do not log credentials, tokens, raw personal data, or secret-bearing URLs.
- Do not report generic checklist items without evidence in changed code.

# Output

Return assets, trust boundaries, findings with severity and exploit path, checks executed, and residual risk.

# Stop conditions

Stop when all changed trust boundaries are traced and no unreviewed attacker-controlled path remains.
