# Quality gates

## Scope gate

Pass when:

- the requirements contract is explicit;
- acceptance criteria and non-goals do not conflict;
- files and public contracts likely affected are identified;
- unresolved questions that block correctness are resolved or exposed.

## Plan gate

Required in standard and deep modes. Pass when every step identifies:

- target file or symbol;
- behavioral change;
- reason this layer owns the change;
- verification method;
- compatibility or rollback constraint.

## Implementation gate

Pass when:

- the diff is limited to approved scope;
- project conventions and existing abstractions are used;
- no debugging artifacts or silent fallbacks remain;
- exceptions are handled at the correct abstraction boundary;
- public and persistence contracts are unchanged unless explicitly approved.

## Verification gate

Pass when:

- checks correspond to changed behavior and risk;
- the narrowest relevant checks run first;
- test results are recorded exactly;
- failures are investigated rather than retried blindly;
- skipped checks and their consequences are disclosed.

## Review gate

Pass when:

- adversarial review used the requirements contract and actual diff;
- no P0 or P1 findings remain;
- P2 findings are either fixed or consciously accepted with reason;
- specialist review was run for triggered risk classes.

## Documentation gate

Required when a change introduces or alters:

- non-obvious invariants;
- operational constraints;
- public behavior;
- architecture boundaries;
- security assumptions;
- irreversible or expensive decisions.

## Completion gate

```yaml
completion_gate:
  acceptance_criteria_satisfied: true
  required_checks_passed: true
  blocking_review_findings: 0
  unrelated_diff: false
  documentation_updated_if_needed: true
  unresolved_assumptions: []
  residual_risks_disclosed: true
```

A task must stop after this gate passes unless the user explicitly expands scope.
