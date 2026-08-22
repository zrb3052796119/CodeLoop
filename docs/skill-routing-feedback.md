# Bounded Skill Routing Feedback

## Authority boundary

`SkillEvidenceLedger` remains a correlation-only shadow report. Production
routing consumes it through `SkillRoutingFeedback`, which has only one power:
it may add `+0.25` or `-0.25` to the ordering score of a Skill that the current
query already admitted independently.

Feedback cannot:

- create lexical, entity, alias, embedding, or explicit-request signal;
- turn abstention into selection;
- remove the only otherwise eligible candidate;
- override an explicit Skill reference;
- cross `(qualifiedName, source, directory, contentDigest)` identity;
- cross the parsed `(intentType, actionType)` task profile;
- promote, rewrite, or roll back a Skill version.

The router preserves the sign of the pre-feedback score and leaves its
specific-signal admission score unchanged. Feedback therefore affects only
the order of candidates already admitted by the deterministic router.

## Evidence gate

A positive or negative adjustment requires all of the following:

- a complete ledger scan with no Run truncation, evaluation truncation, or
  journal diagnostics;
- an exact v1 shadow snapshot and exact Skill digest/profile identity;
- treatment and control cohorts of at least 20 Runs each;
- Wilson 95% intervals that do not overlap and an absolute observed outcome
  delta of at least 0.15;
- complete independent verification observations for both cohorts;
- for positive evidence, every treatment Run verified passed plus at least
  three explicit accepts and no correction/rejection;
- for negative evidence, at least 25% of treatment Runs verified failed plus
  at least three explicit corrections/rejections and no accepts.

The adapter recomputes rates, Wilson intervals, and deltas from bounded cohort
counts. It does not trust the derived statistics in a stored snapshot. A
malformed or duplicated evaluation disables the affected authority, and a
read failure returns an empty feedback set without changing base routing.

## Runtime and audit

Feedback projections are cached for 60 seconds, bounded to 64 workspaces, and
derived from at most the 200 Runs / 100 evaluations already enforced by the
ledger. Refresh errors are cached for at most 10 seconds and logs expose only
the exception type.

Applied adjustments appear in the candidate reason as
`evidence:positive_signal(+0.250)` or
`evidence:negative_signal(-0.250)`. The content-free `skill.routed@v2` event
also records `evidenceAdjustment` on the affected Skill, so the live decision
can be audited while preserving the existing digest join.

Operators can immediately disable this ranking authority with:

```bash
export MINICODE_SKILL_FEEDBACK=0
```

This switch does not disable evidence collection or the Dashboard shadow
report; it only removes the live ranking adjustment.
