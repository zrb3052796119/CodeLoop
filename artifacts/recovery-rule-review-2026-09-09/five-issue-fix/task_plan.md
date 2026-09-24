# Recovery contract fixes

Goal: Implement and verify all five findings from three-state-review, preserving existing work.

- [x] Read findings and snapshot pre-edit sources.
- [x] T1/T2: lossless argv/flag parsing and actual verification mode.
- [x] T3/T4: object-bound path exceptions and top-level argument errors.
- [x] T5: isolate every recovery proof chain.
- [x] Run new red/green tests, previous probes, complete reflection/memory surface.
- [x] Independent review, final fixes, implementation record.

Ownership: Parent parser/evidence mode and integration; consumer reviewer exception helpers/new tests; invariant reviewer synthesis/new tests. No model providers, memory writes, or production wiring needed for these fixes.

Final: 841 tests passed; 43 new regressions also checked against pre-fix snapshots (27 expected failures); 13 probe groups verified; independent review closed. See completion.md.
