# Task Plan: Split Workflow Phase Orchestration

## Goal

Reduce `minicode/tools/task.py` orchestration complexity by moving one coherent,
centrally controlled workflow module behind a small interface, without changing
routing, permissions, artifacts, audit, cancellation, or promotion truth.

## Phases

- [x] Phase 1: Create persistent plan and freeze constraints
- [x] Phase 2: Inventory state, dependencies, callers, and test seams
- [x] Phase 3: Compare three radically different module interfaces
- [x] Phase 4: Add one behavioral seam regression and select the extraction
- [x] Phase 5: Implement the module and migrate the task-tool caller
- [x] Phase 6: Independent review and focused/affected/full verification
- [x] Phase 7: Conventional commit and handoff

## Constraints

- `CentralScheduler` remains the sole dispatch/state-machine authority.
- `task_tool` remains the public caller interface and serialized output remains
  field/byte compatible.
- Human approval, commit-ready persistence, audit/trace finalization, deadline,
  cancellation, and promotion truth cannot cross an ambiguous ownership seam.
- The extracted module must be deep: fewer caller facts, not a bag of helpers.
- Do not introduce a port or adapter for in-process/local-substitutable logic.
- Existing unrelated context-compaction and README changes remain untouched.

## Key Questions

1. Which state cluster can move without passing dozens of mutable fields?
2. Should the seam own a complete workflow run, one phase family, or only
   scheduler-facing ports?
3. Which test observes the public behavior while proving the new seam exists?
4. How much code can leave `task.py` without moving task-tool concerns with it?

## Errors Encountered

- The first AST inventory comprehension treated `ast.Store` as a name node and
  raised `AttributeError`; replaced it with `symtable` plus a corrected AST
  usage analysis.
- One shell-quoted inline Python set lost its string quotes and raised
  `NameError`; reran with single-quoted literals inside the expression.
- One inventory command combined independent reads with `&&`; subsequent
  commands use separate/parallel invocations so output remains scoped.
- One broad dependency search produced truncated output; reran it with a
  bounded result set and recovered the needed references.
- The finalization codemod's postcondition assumed a single-line method
  signature; the preserved source uses a multiline signature. No source file
  had been written yet, so the assertion was corrected and the codemod rerun.
- The first finalization-focused run entered the truthful fail-closed fallback:
  the mechanical nonlocal cleanup had also removed a nested `select_trace`
  closure's own nonlocal declaration. A diagnostic re-raise exposed the exact
  projection error; the nested declaration and production fallbacks were
  restored before rerunning tests.
- The new pre-promotion cancellation regression was initially inserted before
  the preceding test's last three assertions, causing a local `NameError` in
  the new test. The assertions were returned to their original test before
  rerunning the focused set.

## Status

**Complete** — implementation, independent review, focused/affected/full
verification, scoped staging, and conventional handoff are complete.
