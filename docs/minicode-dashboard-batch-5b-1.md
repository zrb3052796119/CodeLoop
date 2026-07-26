# MiniCode Dashboard Batch 5B-1 Implementation

## Delivered scope

Batch 5B-1 adds canonical, content-free observation for real Context compaction,
overflow recovery, and process-local WorkingMemory state. These events flow
through the existing optional Agent event sink into the same Run journal, then
through the strict Dashboard Run Detail read model and Timeline UI.

The implementation intentionally does not add cross-run Context or
WorkingMemory aggregation. Overview and Ops remain unchanged; Memory Lifecycle
labels cross-run aggregation as unavailable until Batch 5B-2.

## Interfaces left for Batch 5B-2

- Journal event types: `context.compacted`, `recovery.started`,
  `recovery.completed`, and `working_memory.observed`.
- Context correlation: `contextOperationId` is shared across one recovery start,
  successful compaction, and recovery completion.
- Run Detail coverage: `context: partial` and `workingMemory: partial`.
- Read-model input: sanitized per-Run journal events only. Batch 5B-2 can build
  aggregate projections from these events without reading Agent internals or
  WorkingMemory content.

## Deliberate limits

- No message, summary, error, prompt, user input, or WorkingMemory entry content
  is persisted or displayed.
- No new HTTP write endpoint or business API is introduced.
- No Session, MemoryPipeline, Tool, Skill, MCP, permission, or TUI behavior is
  changed.
- WorkingMemory observation is process-local and is not evidence that every
  compaction implementation consumes the tracker.
- Canonical wiring covers the reachable, reliable pre-request and reactive
  paths. Legacy predictive/forced paths that discard results or call mismatched
  APIs are not presented as successful observations.
