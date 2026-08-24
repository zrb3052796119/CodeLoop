# MiniCode Main/Sub-agent Model Routing Live Acceptance

Date: 2026-08-23

Public machine-readable companion:
[sanitized routing projection](../artifacts/model-routing-live-acceptance-2026-08-23.json).
It intentionally excludes prompts, response content, headers, credentials, and
raw local sidecars; it is a curated acceptance projection, not an independently
signed provider receipt.

## Re-acceptance Update — PASS (Latest Configuration)

After the user updated the role-specific Qwen model IDs, two new independent
live rounds passed all four actors.

| Actor | Outbound request-body `model` | HTTP | Provider top-level `model` | Nested outcome | Repeat |
|---|---|---:|---|---|---|
| Parent | `deepseek-v4-pro` | 200 | `deepseek-v4-pro` | completed | same result twice |
| Explore | `qwen3.7-plus` | 200 | `qwen3.7-plus` | completed | same result twice |
| Plan | `qwen3.7-max` | 200 | `qwen3.7-max` | completed | same result twice |
| General | `qwen3.7-max` | 200 | `qwen3.7-max` | completed | same result twice |

Latest overall verdict: **PASS (4 of 4 actors usable)**.

In both rounds:

- every actor made exactly one HTTP request, so success did not depend on a
  retry or hidden fallback;
- every outbound `model` matched the effective role configuration;
- every successful provider response self-reported the same model;
- all four expected synthetic sentinel responses were observed;
- the parent Run completed, and every child sidecar recorded one
  `model.started`, one `model.completed`, and outcome `completed`;
- no project content, persistent Memory, response body, request header, or
  credential value was retained by the probe.

Successful usage was also bounded and consistent: Parent 168 input tokens in
each round; Explore 3,737/3,740; Plan 3,868/3,871; General 6,143/6,146.  Output
token variation was expected and did not alter any sentinel or identity check.

Focused route/Task regression verification also passed again:
`3 passed, 48 deselected`.

The initial failure below is retained as historical evidence of the previous
configuration and is superseded by this re-acceptance result.

## Historical Initial Verdict — Superseded

The configured model identity is routed correctly at the HTTP request
boundary, but the configured system is not fully operational:

- Parent routing and availability: **PASS**
- Plan sub-agent routing and availability: **PASS**
- Explore sub-agent routing: **PASS**; provider availability: **FAIL**
- General sub-agent routing: **PASS**; provider availability: **FAIL**

The initial live acceptance was **FAILED (2 of 4 actors usable)**.
This is not a silent fallback: the failing child roles requested their exact
configured model names and the configured Qwen endpoint rejected those names.

## Historical Effective Configuration

| Actor | Provider / endpoint | Effective model |
|---|---|---|
| Parent | custom / `api.deepseek.com` | `deepseek-v4-pro` |
| Explore | OpenAI-compatible / `token-plan.cn-beijing.maas.aliyuncs.com` | `qwen3.7-flash` |
| Plan | OpenAI-compatible / `token-plan.cn-beijing.maas.aliyuncs.com` | `qwen3.7-plus` |
| General | OpenAI-compatible / `token-plan.cn-beijing.maas.aliyuncs.com` | `qwen3-coder-plus` |

The parent model, provider, and custom credential all resolve from the private
user Env profile.  All four routes construct `OpenAIModelAdapter`; credential
values were neither printed nor persisted by the probe.

## Historical Live Results

Two independent, identical synthetic runs were performed.

| Actor | Outbound request-body `model` | HTTP | Provider top-level `model` | Nested Run outcome | Repeat |
|---|---|---:|---|---|---|
| Parent | `deepseek-v4-pro` | 200 | `deepseek-v4-pro` | completed | same result twice |
| Explore | `qwen3.7-flash` | 404 | unavailable | failed (`Model not exist`) | same result twice |
| Plan | `qwen3.7-plus` | 200 | `qwen3.7-plus` | completed | same result twice |
| General | `qwen3-coder-plus` | 404 | unavailable | failed (`Model not exist`) | same result twice |

Each live run produced one parent `model.started` event and three joined
sub-agent sidecars.  Every child sidecar recorded one `model.started`; Plan
recorded `model.completed`, while Explore and General recorded `model.failed`.
The parent Run also recorded three paired `task` start/finish operations and
three `subagent.completed` projections.

Successful request usage, included only as corroboration:

- Parent: 178 input / 30 output tokens in both runs.
- Plan: 3,889 input / 27 output tokens in run 1; 3,892 input / 86 output
  tokens in run 2.

## Acceptance Method (Used for Both Runs)

1. Load the canonical runtime against a newly created empty workspace.
2. Construct the parent through `create_model_adapter` and children through
   the production `task` tool's nested Agent loop.
3. Apply context-local actor labels (`parent`, `subagent:explore`,
   `subagent:plan`, `subagent:general`).
4. At the exact `open_verified_url` boundary, capture only endpoint host/path,
   request-body `model`, HTTP status, provider top-level `model`, and token
   usage.  Headers, messages, response content, and credentials are excluded.
5. Join the captured actor with canonical parent and sub-agent Run journals.
6. Repeat under the same conditions and require the result to reproduce.

The empty workspace and isolated Memory root prevented repository content or
persistent Memory from entering the provider requests.  Skill embedding,
hybrid Memory retrieval, and remote reflection were disabled so the only
network activity under observation was the four intended chat actors.

## Static Corroboration

- `OpenAIModelAdapter` writes `self.runtime["model"]` to the outbound request.
- The `task` tool passes the current child role to
  `create_subagent_model_adapter`, which overrides the child runtime with that
  role's model and endpoint before starting the nested Agent loop.
- Focused regression verification: `3 passed, 48 deselected` across the
  per-role route and Task integration tests.

## Historical Interpretation — Resolved

At the time of the initial run, MiniCode was doing what the configuration
asked; there was no evidence that the
parent model leaks into child calls or that children silently fall back to one
shared model.  The remaining defect is configuration/provider compatibility:
the selected Qwen endpoint does not recognize `qwen3.7-flash` or
`qwen3-coder-plus` for this account/deployment.

Before declaring the system fully configured, replace those two role values
with exact model IDs available on the configured endpoint (or use an endpoint
that exposes the intended IDs), then rerun this four-actor acceptance.  The
working `qwen3.7-plus` route proves that the child credential, base URL, and
OpenAI-compatible transport themselves are valid.

That historical follow-up is now resolved: Explore uses `qwen3.7-plus`, while
Plan and General use `qwen3.7-max`; the latest two-round acceptance above
proves all three child routes are recognized and operational.

Provider `model` is a provider-reported identity.  It corroborates request
identity but cannot independently prove how a provider implements an alias
behind its API boundary.
