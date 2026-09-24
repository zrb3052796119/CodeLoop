# Memory Retrieval Production Baseline v40

## Certification

`memory-retrieval-production-v40` is the active production-source baseline.
Its parent is `memory-retrieval-production-v39`.

- Manifest: `tests/fixtures/memory_retrieval_production_freeze/v40.json`
- Manifest SHA-256: `52d7131db710ef61674e776f931426e6efa8e91764df95eaa86612295f79e4f7`
- Protected production files: 73
- Changed protected files: 42
- Files added to protection: 11
- Removed files: 0
- Candidate match: true
- Current files match: true
- Manifest integrity: v1-v40 all true

The final pre-certification `minicode/memory.py` SHA-256 was
`e8dac517909e26841635fef2547fff08a20a649888bdff426b93e79da130581e`.

## Scope

v40 reconciles the inherited v39 protected source set and extends protection to
new Memory authority dependencies. Its changed paths span persistent Memory,
retrieval, context compaction, Skill routing, multi-agent workflow, and client
surface tracks, so it is not described as a pure Memory delta. The 73 protected
paths are a deliberately bounded certification set, not a claim that every
transitive production dependency in all six tracks is frozen.

Every changed or newly protected file has one closed reason code. Fourteen
paths have no current worktree delta from `HEAD`: their v39 hashes came from a
different source state, so v40 labels them explicitly as source-state
reconciliation instead of inventing a feature provenance.

| Reason code | Changed | Added to protection |
|---|---:|---:|
| `persistent_memory_lifecycle_authority` | 16 | 10 |
| `persistent_memory_retrieval_semantics` | 3 | 1 |
| `context_compaction_fidelity_authority` | 1 | 0 |
| `skill_routing_semantic_authority` | 3 | 0 |
| `multi_agent_workflow_authority` | 4 | 0 |
| `client_surface_runtime_integration` | 1 | 0 |
| `production_source_state_reconciliation` | 14 | 0 |
| **Total** | **42** | **11** |

## Exact v39 to v40 lineage

### Persistent Memory lifecycle authority

Changed:

- `minicode/agent_loop.py`
- `minicode/agent_reflection.py`
- `minicode/conversation.py`
- `minicode/conversation_turn_store.py`
- `minicode/memory.py`
- `minicode/memory_approval.py`
- `minicode/memory_curator_agent.py`
- `minicode/memory_pipeline.py`
- `minicode/memory_store.py`
- `minicode/project_memory_deletion.py`
- `minicode/run_events.py`
- `minicode/run_journal.py`
- `minicode/run_lifecycle.py`
- `minicode/session.py`
- `minicode/storage_health.py`
- `minicode/web/read_model.py`

Added to protection:

- `minicode/advisory_lock.py`
- `minicode/curator_schedule.py`
- `minicode/memory_approval_interpretation.py`
- `minicode/memory_audit_contract.py`
- `minicode/memory_authority_io.py`
- `minicode/memory_authority_publisher.py`
- `minicode/memory_blob_store.py`
- `minicode/memory_provenance.py`
- `minicode/memory_write_contract.py`
- `minicode/project_facts.py`

`addedFiles` means first added to this certification's protection set; it does
not imply that every listed file was newly created in Git.

### Persistent Memory retrieval semantics

Changed:

- `minicode/memory_candidate_consolidation.py`
- `minicode/memory_injector.py`
- `minicode/memory_retrieval.py`

Added to protection:

- `minicode/memory_lexical_policy.py`

### Context compaction fidelity authority

Changed:

- `minicode/context_compactor.py`

### Skill routing semantic authority

Changed:

- `minicode/agent_runtime.py`
- `minicode/main.py`
- `minicode/tui/input_handler.py`

### Multi-agent workflow authority

Changed:

- `minicode/headless.py`
- `minicode/permissions.py`
- `minicode/tooling.py`
- `minicode/tools/task.py`

### Client surface runtime integration

Changed:

- `minicode/web/static/assets/app.js`

### Production source-state reconciliation

These 14 paths differ from their v39 manifest hashes but have no current
worktree diff from `HEAD`. The reason code records that bounded reconciliation
fact and must not be reused as a generic future baseline escape hatch.

Changed:

- `minicode/deletion_store.py`
- `minicode/gateway.py`
- `minicode/permission_approval.py`
- `minicode/tools/__init__.py`
- `minicode/tools/network_safety.py`
- `minicode/tools/run_command.py`
- `minicode/tools/search_providers.py`
- `minicode/tools/web_search.py`
- `minicode/workspace.py`
- `minicode/web/change_feed.py`
- `minicode/web/chat_http.py`
- `minicode/web/chat_stream.py`
- `minicode/web/static/assets/styles.css`
- `minicode/web/static/index.html`

Every other v39-protected file retains its v39 digest. Historical manifests
v1-v39 and their pinned hashes remain byte-identical.

## Verification contract

The v40 verifier requires all of the following:

- v1-v40 manifest hashes match their immutable pins;
- v39 to v40 lineage is exactly 42 changed, 11 added, and zero removed files;
- every changed and added path has its declared per-file reason code;
- the deterministic candidate equals the accepted v40 manifest;
- all 73 current protected source files match the accepted digests; and
- historical lineage through v39 remains unchanged.

The v40 writer validates every historical pin and writes only the fixed
`v40.json` target. Tests cover the literal delta, unrelated-parent preservation,
fixed-target writing, missing dependencies, unexpected parent changes,
representative tampering for every track, deterministic output across working
directory/HOME/hash seed, and the active verifier.

Verification at certification time:

- v40-specific contract: 16 passed;
- complete production-baseline compatibility set: 154 passed;
- active verifier: `matches=true`, `candidateMatches=true`,
  `currentFiles.matches=true`, 73/73 protected files, and v1-v40 manifest
  integrity all true.

## Boundary

v40 certifies production source identity and lineage only. It does not rewrite,
accept, or re-sign a semantic behavior artifact. Any semantic-gold version
change remains a separate review and certification step performed against this
active source baseline.
