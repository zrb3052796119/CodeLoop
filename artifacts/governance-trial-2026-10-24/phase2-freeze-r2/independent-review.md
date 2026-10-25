# Phase2 replacement freeze: independent read-only review

GO for artifact consistency. Root source `8b15640ab14316873596e921a7b6dda116320672` retains P0 `minicode` bytes from `9f936942`; only the transport script and its tests differ in the scoped runtime/test paths. No API call, runtime-settings load, or source modification occurred in this review.

- Old manifest SHA-256: `987f7cf31627ca85f18cdf2a270dae3918c5bf6cacac68cf02053dd767da91be`.
- R2 manifest SHA-256: `32dacc257169eff53026a3b8e1663ed68b62ceae1adac118589073921d47d332`.
- The only manifest changes are `createdAt`, `frozenCodeArchiveSha256`, `harnessSha256`, and `taskSha256`. All model/route/limits/price/role settings and the complete ordered 32-cell schedule are identical (16 cases, single/auto, one repeat, concurrency 1).
- Study design, analysis plan, and support inventory are byte-identical. All 999 support files in each directory match their hashes. All prepared source inventories and normalized oracle hashes match the registered design.
- Tasks are byte-identical after exactly 8 substitutions of the absolute `oracle-support` prefix. Only `oracle_source` changes, in 4 cases; prompts, files, authorization, verification policy, membership, and order remain identical.
- Both ZIP member sets contain 330 entries. The sole changed member is `scripts/evaluation_transport.py`, SHA-256 `b3034785c2268067b0503731aadafb4f29b5c36df5141b98e1a4fa1c69232534`. R2 runtime members match the current ROOT source. Independently recomputed production/harness/archive hashes match both manifests.
- Neither frozen directory has `trials`, `private`, `results.json`, a transport database, or an execution receipt at review time. The old freeze remains intact.

This review approves consistency of the replacement freeze only. Root must retain and version the old preregistration withdrawal and publish the replacement before execution. It does not constitute live-run acceptance or override the launch checks. Full machine-readable findings, timestamps, source hashes, and configuration are in `r2-independent-review.json`.
