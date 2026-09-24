# Plan: Recovery rule synthesis review

Goal: Review the current persistent-memory recovery learning chain and assess a dedicated child Agent design, with concrete code evidence. This is an architectural review; implementation changes are outside this turn's scope.

- [x] Locate current source and review guidance; preserve all existing working-tree edits.
- [x] Trace production evidence, synthesis, validation, and persistence paths.
- [x] Independently review semantic hardcoding and available child Agent infrastructure.
- [x] Run focused offline checks/probes for material findings.
- [x] Deliver a concrete design and transition recommendation.

Status: Complete. review.md contains verified findings, the proposed specialized Agent design, transition order, and offline validation limits. Both probe scripts were rerun successfully.

Errors: Initial broad search output was too large; use bounded source reads. tests/conftest.py is absent; selected test modules provide their own fixtures.
