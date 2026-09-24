# Multi-agent architecture review

Goal: assess active agents, responsibility boundaries, scheduling, and prioritized improvements in the current root Python runtime. Review only; preserve existing worktree changes.

- [x] Identify production package and delegate independent review areas.
- [x] Map parent, worker roles, runtime controllers, and historical implementations.
- [x] Verify concrete findings with source and targeted offline tests.
- [x] Deliver Chinese evidence-backed review and optimization priorities.

Status: review complete. 490 relevant offline tests passed; four probes saved and their outputs reviewed. Existing root task_plan.md and notes.md belong to other work and are preserved; this directory holds this review's planning files.

Errors: no blocking errors. One guessed scope-registry filename did not exist; rg resolved minicode/workflow_scope_registry.py. Initial inventory command ended with exit 1 because no local .venv exists; Python 3.12 is available.
