# Canonical Hybrid Memory Retrieval

Hybrid Memory is now wired into `MemoryPipeline.read()` and
`MemoryPipeline.inject()` behind an evidence-gated, default-off runtime switch.
The lexical retriever remains the default when the feature is not requested or
its model/evidence cannot be initialized.

## Production protocol

The allowlisted protocol is `hybrid-canonical-v4`:

1. Reject lifecycle-ineligible, unsafe, locked, and archival entries before
   embedding or model adjudication.
2. Reject an underspecified query with no concrete object or file context.
3. Generate up to 20 dense candidates with one evidence-bound encoder: either
   the pinned local multilingual E5 model or the allowlisted DashScope Qwen
   `text-embedding-v3` adapter. Union them with deterministic lexical
   admissions, capped at 32.
4. Ask the strict relevance verifier for structured decisions.
5. Challenge preliminary admissions. Only high-confidence
   `contradictory_order`, `contradictory_polarity`, or `path_conflict` results
   can veto; other challenger outputs are diagnostic and cannot grant access.
6. Render through the existing canonical consolidation and token-budget path.

The model-call ceiling is eight per agent turn. Identical query/authority
snapshots use a bounded cache, so a subsequent inject does not repeat the
remote calls. Once Hybrid Memory is active, a malformed response, exhausted
call budget, or provider exception renders no memory for that read. It does not
silently restore lexical admissions. If Hybrid Memory was never activated,
lexical behavior is unchanged.

Remote Qwen activation probes three fixed synthetic canaries before it sends a
memory document. Endpoint, model ID, dimension, representation version, and
the normalized canary-vector fingerprint must exactly match promotion evidence.
Provider drift or an unavailable credential fails provider initialization.

## Promotion evidence

Local E5 evidence is
`artifacts/memory-retrieval-hybrid-v4-production-evidence.json`, whose pinned
fingerprint was derived from the untouched v5 holdout:

- dense candidate recall@20: 1.00;
- post-decision positive recall: 1.00;
- rendered positive recall and precision: 1.00 / 1.00;
- hard-negative render rate: 0;
- unsafe/lifecycle index leakage: 0;
- provider fallbacks: 0.

Remote Qwen evidence is
`artifacts/memory-retrieval-hybrid-qwen-v1-production-evidence.json`, whose
pinned fingerprint is
`bd317c42adb2d9d21807add9030ef111a197b9817bb88df6f24651600397e61e`.
It was derived from the independently frozen v7 holdout:

- dense candidate recall@20: 1.00;
- post-decision and rendered positive recall: 0.875 / 0.875;
- rendered precision: 1.00;
- hard-negative render rate: 0;
- unsafe/lifecycle index leakage: 0;
- provider fallbacks: 0.

All cases are synthetic. These results authorize this exact configuration;
they are not a claim about production traffic. Failed v2-v4 reports and the
transport-invalidated v6 Qwen report remain in `artifacts/` as audit history
rather than being overwritten. v6 achieved candidate recall 1.00 but every
verifier call failed closed because the old chat adapter did not load a usable
CA bundle. The shared verified-TLS seam was fixed before v7 was authored.

## Local E5 installation

Local E5 keeps only the embedding computation on-device. The certified
verifier/challenger route is still remote: it receives the retrieval query and
candidate Memory text/metadata. Do not enable Hybrid Memory for content that
must not leave the machine unless that remote disclosure is acceptable. The
same verifier/challenger disclosure applies to the Qwen activation below; Qwen
additionally sends the query and approved Memory text to its embedding API.

Install the optional local runtime, then install the exact pinned model into a
new directory:

```bash
python -m pip install -e '.[memory-hybrid]'
python -m scripts.install_memory_hybrid_model \
  --target /absolute/path/to/multilingual-e5-small
```

The installer pins the repository revision, verifies every downloaded file,
does not execute remote model code, and writes the certified manifest
atomically. It refuses an existing target.

Enable it in `~/.mini-code/settings.json`:

```json
{
  "memoryHybrid": {
    "enabled": true,
    "embeddingProvider": "local-e5",
    "modelPath": "/absolute/path/to/multilingual-e5-small",
    "evidencePath": "/absolute/path/to/CodeLoop/artifacts/memory-retrieval-hybrid-v4-production-evidence.json",
    "verifierModel": "deepseek-chat"
  }
}
```

## Reusing Qwen without a local model

Qwen reuses the same `MINICODE_EMBEDDING_*` client configuration as Skill
routing. No ONNX runtime or 113 MB model directory is required. Remote memory
embedding still requires a separate authorization because approved persistent
memory text and the current retrieval query leave the machine; merely
configuring a Skill embedding key does not grant that authority.

```json
{
  "memoryHybrid": {
    "enabled": true,
    "embeddingProvider": "qwen",
    "allowRemoteEmbedding": true,
    "evidencePath": "/absolute/path/to/CodeLoop/artifacts/memory-retrieval-hybrid-qwen-v1-production-evidence.json",
    "verifierModel": "deepseek-chat"
  }
}
```

The Qwen adapter is allowlisted only for DashScope
`text-embedding-v3` at the certified endpoint. A different model, compatible
proxy, dimension, or canary result is a different adapter and needs new
evidence. Skill-vector and memory-vector caches are intentionally separate.

Equivalent environment variables are:

- `MINI_CODE_MEMORY_HYBRID_ENABLED`
- `MINI_CODE_MEMORY_HYBRID_EMBEDDING_PROVIDER` (`local-e5` or `qwen`)
- `MINI_CODE_ALLOW_REMOTE_MEMORY_EMBEDDING`
- `MINI_CODE_MEMORY_HYBRID_MODEL_PATH`
- `MINI_CODE_MEMORY_HYBRID_EVIDENCE_PATH`
- `MINI_CODE_MEMORY_HYBRID_VERIFIER_MODEL`
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL` (defaults to `https://api.deepseek.com`)

The verifier model is independent of the main coding model. The promoted
configuration pins `deepseek-chat`, so an Anthropic, OpenAI, OpenRouter, Qwen,
or other primary route must set the dedicated `DEEPSEEK_API_KEY` (and, if
needed, `DEEPSEEK_BASE_URL`) in `~/.mini-code/.env`. A primary DeepSeek custom
route may reuse `CUSTOM_API_KEY` only when the selected model is DeepSeek and
the endpoint is the official `https://api.deepseek.com` route; credentials for
any other custom endpoint are never inherited by the verifier. The
`verifierModel` field shown above records the evidence-bound value; it is not a
free model selector under this promotion artifact. If the dedicated adapter
cannot be built, Hybrid activation is declined and the normal lexical
retriever remains active rather than borrowing the primary model's transport.

## Verification

The production evidence can be exercised without real memories:

```bash
python -m scripts.memory_hybrid_production_smoke \
  --model-path /absolute/path/to/multilingual-e5-small
```

For Qwen, no model path is used:

```bash
python -m scripts.memory_hybrid_production_smoke \
  --embedding-provider qwen
```

The smoke query supplies both an after-commit rule and its before-commit
opposite. A passing run activates the allowlisted evidence and renders only the
after-commit memory.
