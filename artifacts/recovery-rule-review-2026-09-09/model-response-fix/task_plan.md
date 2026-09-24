# Fix reflection provider timeout and empty output

Goal: Diagnose and repair the dedicated recovery model path so real synthetic episodes yield usable, bounded structured responses without changing the primary agent or durable-memory selection.

- [x] Capture provider finish/reasoning metadata and verify request controls/capabilities.
- [x] Reproduce the failure, fix request controls and local-deadline handling with regression tests.
- [x] Run a bounded real positive probe, then replay the representative same-episode evaluation.
- [x] Review actual generated claims, repair omitted failed retry evidence, verify both affected candidates, run relevant tests, and document the root cause and remaining limitations.

User explicitly requested repair; current remote reflection opt-in remains authoritative. Only synthetic fixtures go to the provider. No credential, endpoint, or reasoning body in artifacts. No automatic switch to durable claim selection.
