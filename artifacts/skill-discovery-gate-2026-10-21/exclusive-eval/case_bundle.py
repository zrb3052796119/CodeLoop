"""Frozen, local-only 20-case fixture. No provider or production router edits."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LEGACY = HERE / "legacy_fixture_builder.py"
CATALOG_SIZE = 1000
FIXTURE_VERSION = "exclusive-body-v1"


def _legacy() -> Any:
    spec = importlib.util.spec_from_file_location("three_arm_legacy_fixture", LEGACY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NEW_SKILLS = {
    "text/canonical-identifiers": (
        "Audit Unicode identifier collisions under a declared normalization and case policy.",
        "unicode",
        "Read the declared normalization form and case sensitivity. Normalize each identifier with that form before grouping record IDs. Keep accents and letter case unless the policy explicitly removes them; do not apply identifier normalization to passwords. Report collisions separately from visually similar but distinct keys.",
    ),
    "storage/conditional-write": (
        "Review stale resource updates using ETag, If-Match and compare-and-swap preconditions.",
        "conditional_write",
        "Compare the supplied If-Match token with the current resource version before applying any field update. A mismatch is a failed precondition and must preserve all stored fields and the version. Distinguish this write precondition from If-None-Match conditional GET caching. Trace the implementation and state the required HTTP outcome.",
    ),
    "data/spreadsheet-export-safety": (
        "Audit CSV cell exports for spreadsheet formula execution while preserving ordinary numeric values.",
        "csv_cells",
        "Read the destination policy, including ignored leading whitespace and the allowed numeric grammar. Classify each cell by its first effective character and the full numeric grammar, then apply the configured escape prefix to the original value when required. CSV quoting alone does not neutralize a spreadsheet formula. Preserve literal data and report record IDs instead of executing any cell.",
    ),
    "timezone/repeated-local-hour": (
        "Resolve a repeated daylight-saving local timestamp using timezone transitions and UTC evidence.",
        "dst_overlap",
        "Determine whether the local wall-clock time has two valid offsets. Convert each candidate independently to UTC, order candidates by UTC, and reconcile them with the recorded server timestamp. Report the selected offset and fold; do not use the current offset, confuse a repeated hour with a nonexistent spring-forward time, or silently assume a unique timestamp.",
    ),
}

# Nearby workflows have genuinely different procedures; none is a target alias.
NEIGHBORS = {
    "http/retry-observability": ("Explain HTTP retry counters and dashboard sampling, without choosing replay safety.", "Compare attempted, completed and sampled request counters. Distinguish request IDs from attempt IDs, audit sampling intervals, and reconcile dashboard aggregation before changing instrumentation."),
    "http/conditional-get": ("Explain conditional GET cache validation with ETag and If-None-Match.", "Compare the cached entity tag with the current response tag. A matching validator permits a 304 response with no entity body. Do not apply GET cache semantics to write preconditions."),
    "http/connection-reuse": ("Diagnose outbound socket reuse and HTTP keep-alive pool exhaustion.", "Compare active, idle and pending sockets over time; trace response closure and idle expiration. Separate resource leaks from slow servers before changing pool capacity."),
    "database/plan-format": ("Convert an already captured SQL execution plan to a stable report format.", "Preserve node hierarchy, actual versus estimated rows and units. Validate serialization round trips; formatting does not establish the cause of a slow query."),
    "database/lock-waits": ("Diagnose blocked SQL transactions using lock owners and wait chains.", "Build the waiter-to-holder graph, locate the oldest blocker, and compare transaction boundaries. An index suggestion alone does not demonstrate the source of a lock wait."),
    "database/index-rollout": ("Plan a safe production rollout of an already approved database index.", "Check online-build support, lock duration, disk headroom and a rollback plan. Verify index validity after building; obtain the approved definition before rollout planning."),
    "auth/session-expiration": ("Diagnose expired authentication sessions and refresh-token rotation.", "Compare issue and expiry times, issuer, audience and refresh reuse records. Session validity and endpoint authorization grants are distinct decisions."),
    "auth/grant-report": ("Export existing role and scope assignments into an audit inventory.", "Read the authoritative assignment snapshot, retain principal-to-scope relationships and mark inherited grants. Report completeness without proposing broader access."),
    "config/format-preservation": ("Format configuration documents without changing values or source precedence.", "Parse and serialize with a stable style, preserve comments when supported, and compare semantic trees. Do not infer runtime effective values from formatting alone."),
    "config/secret-rotation": ("Plan staged credential rotation across services and configuration stores.", "Identify consumers, dual-validity windows and rollback requirements. Verify new credentials before revoking old ones, and avoid printing secret values."),
    "queue/ledger-schema": ("Migrate an existing processed-message ledger schema safely.", "Inventory existing uniqueness constraints, backfill stable keys, then add constraints with an operational rollback. Schema migration is separate from consumer side-effect transaction design."),
    "queue/visibility-timeout": ("Diagnose queue visibility timeouts and delayed acknowledgement delivery.", "Compare processing duration with visibility renewal intervals. Distinguish lease expiry from committed delivery state and explain redelivery windows without assuming exactly-once delivery."),
    "api/error-localization": ("Translate human-readable API error messages while preserving machine fields.", "Keep status codes and stable error identifiers unchanged. Localize only display text, verify fallback language, and avoid exposing internal diagnostics."),
    "api/schema-diff": ("Compare versioned API request schemas for backward compatibility.", "Check requiredness, type narrowing, removed properties and enum changes. Evaluate documented request clients; response error behavior needs its own implementation evidence."),
    "ci/artifact-publish": ("Diagnose artifact publication failures after successful build and test stages.", "Read the first failing publication response, registry credentials and artifact identity. Do not retry tests when the evidence shows the failure begins after tests have passed."),
    "ci/report-format": ("Format completed CI test results into machine-readable reports.", "Preserve test identities, outcomes, durations and retries. Validate totals against raw results; presentation cannot establish shared-fixture isolation."),
    "ci/cache-key": ("Diagnose stale CI dependency caches and cache key collisions.", "Compare lockfile hashes, platform tags and restore prefixes. Reproduce with an isolated cache and avoid treating a warm-cache pass as proof of test order independence."),
    "checkout/payment-dashboard": ("Explain checkout payment dashboard aggregation and metric sampling.", "Reconcile numerator, denominator and time window from recorded events. Separate sampling artifacts from latency component measurements before changing instrumentation."),
    "checkout/receipt-format": ("Format checkout receipts using settled amounts and currency precision.", "Use authoritative settled amounts, apply currency minor-unit rules and retain order identity. Receipt layout is unrelated to provider retry resilience."),
    "text/case-insensitive-lookup": ("Design case-insensitive text lookup using an explicit case-folding policy.", "Define locale and Unicode casefold behavior before indexing. Compare folded keys and preserve original display labels; case folding is not canonical Unicode normalization."),
    "text/search-stemming": ("Tune search token stemming and language-specific term recall.", "Separate tokenization, stemming and ranking effects using held-out queries. Stem search terms rather than stored identity keys, and preserve exact-match identifiers."),
    "text/encoding-repair": ("Diagnose mojibake caused by incorrect byte-to-text decoding.", "Retain raw bytes, identify the declared encoding, and test reversible decodes. Never treat a correctly decoded combining sequence as encoding corruption."),
    "storage/object-retention": ("Audit immutable object retention and legal-hold expiration.", "Compare retention timestamps, object versions and hold overrides before deletion. Distinguish retention authorization from resource concurrency tokens."),
    "storage/write-retry": ("Assess retry safety for object-store writes after a lost acknowledgement.", "Use operation identity and recorded version history to determine whether a retry repeats a committed write. A replay key and an If-Match stale-write precondition solve different problems."),
    "data/csv-dialect": ("Infer CSV delimiters, quoting and newline dialect from raw records.", "Check candidate delimiters against a consistent column count and quoted embedded newlines. Preserve data values; correct CSV syntax does not prevent spreadsheet formula execution."),
    "data/spreadsheet-number-format": ("Choose spreadsheet display formats for imported dates and decimal values.", "Separate numeric cell values from display formats and locale separators. Preserve exact decimal precision; formatting does not sanitize active formulas."),
    "timezone/spring-gap": ("Resolve nonexistent local times during daylight-saving spring transitions.", "Identify the forward transition and reject or shift nonexistent wall-clock values according to the declared policy. A gap has no valid instant; a repeated hour has two."),
    "timezone/recurring-schedule": ("Design recurring wall-clock schedules across timezone offset changes.", "Anchor recurrence to a named zone and local rule, convert each occurrence independently, and document gaps and overlaps. Do not extrapolate a fixed UTC offset across a season."),
    "logging/retention": ("Audit log retention duration and rollover boundaries.", "Compare event timestamps, partition boundaries and retention policies. Preserve audit requirements while identifying expired partitions; do not conflate storage retention with credential redaction."),
    "cache/stampede": ("Diagnose simultaneous cache misses and backend request amplification.", "Compare expiry times with concurrent miss rates, then assess single-flight or refresh-ahead behavior. A stampede and stale-price invalidation are different failure modes."),
    "architecture/plugin-boundary": ("Review plugin dependency direction and extension-point contracts.", "Map host-to-plugin interfaces and lifecycle ownership. Keep optional plugin imports behind stable interfaces and test unload behavior separately from a concrete Python import cycle."),
    "docs/config-reference": ("Write a configuration reference from validated setting definitions.", "Describe accepted types, defaults, precedence and examples from authoritative definitions. Do not infer an application's startup command from a configuration table."),
}


# Body-exclusive cases: the answering constants exist ONLY in these bodies.
# Evidence files carry raw data plus a policy_source pointer to the internal
# skill library; no workspace file repeats the constants.
EXCLUSIVE_SKILLS = {
    "http/bounded-replay-contract": (
        "Apply the platform bounded replay contract to committed outbound POSTs.",
        "Compute the documented replay budget as attempts_limit x (per-attempt timeout + jitter reserve), using the platform constants attempts_limit = 4 and jitter reserve = 250 ms. A committed POST may be replayed automatically only when the computed budget is at least 2000 ms. Report the budget, the limit and the decision; do not infer the constants from observed traffic."
    ),
    "auth/scope-diff-convention": (
        "Report minimal missing authorization scopes with the platform scope naming convention.",
        "Use the platform convention: a fully qualified scope is shop.<resource>.<action>. A wildcard shop.<resource>.* satisfies read-class actions but never satisfies the export action. Report exactly one most-specific missing scope string. Do not invent alternate separators, plurals or prefixes."
    ),
    "cache/purge-holdoff": (
        "Enforce the documented purge holdoff window after cache generation bumps.",
        "The platform holdoff after a generation bump is 90 seconds. A second bump inside an active holdoff extends the current window by 45 seconds counted from the later bump. Repeated purge requests inside the window are held; report the exact window end timestamp in UTC."
    ),
    "returns/restock-grade": (
        "Grade customer returns and compute restock credit with the platform restock schedule.",
        "Grade A when received within 7 days of delivery with intact packaging; grade B within 14 days with intact packaging; damaged packaging caps the grade at C regardless of timing; beyond 14 days the return is refused with zero credit. Restock credit multiplies the item price by the platform factors A = 0.95, B = 0.90, C = 0.70."
    ),
    "webhooks/signature-skew": (
        "Verify webhook signatures within the documented clock-skew and nonce windows.",
        "Accepted clock skew is 300 seconds in either direction between sent and received timestamps. Replay protection accepts each nonce once within 600 seconds. A skew breach answers 401; a nonce replay answers 409; a valid event answers 200. Apply skew before nonce checks."
    ),
    "database/plan-alert-thresholds": (
        "Flag saved SQL plans with the platform plan-alert thresholds.",
        "Flag a plan only when the scan-to-result ratio is at least 50 AND rows returned are at least 8. When an index is proposed, order columns by the platform convention: equality predicates first, then range predicates, then order-by columns."
    ),
    "checkout/latency-slo-split": (
        "Split the checkout latency budget across components with the documented SLO shares.",
        "The platform shares of the total checkout budget are: gateway 30%, payment_api 45%, presentation 25%. A component breaches its SLO when its measured p95 exceeds its own share budget. Report each component budget and the breaching components sorted alphabetically."
    ),
    "queue/commit-safety-margin": (
        "Apply the documented queue acknowledgement deadline and commit safety margin.",
        "The acknowledgement deadline is 45 seconds after first delivery, and the platform commit safety margin is 10 seconds: a consumer must commit no later than deadline minus margin. Commits after that effective point are late and treated as redeliveries, keeping the original message ID."
    ),
}

SUBSYSTEMS = {
    "billing": "invoice ledger", "warehouse": "pick-wave allocator", "search": "search ranking service",
    "tax": "tax rate resolver", "shipping": "carrier booking gateway", "fraud": "risk scoring pipeline",
    "catalog": "product catalog", "recommendations": "recommendation feed", "notifications": "notification fanout",
    "returns": "return authorization", "subscriptions": "renewal scheduler", "loyalty": "reward points ledger",
    "analytics": "event aggregation", "experiments": "experiment assignment", "marketing": "campaign delivery",
    "identity-sync": "directory synchronization", "media": "media transcoding", "inventory": "stock reservation",
    "reporting": "report materialization", "fulfilment": "fulfilment planner", "pricing-rules": "price rule evaluation",
    "promotions": "coupon redemption", "vendor-import": "vendor feed ingestion", "audit-trail": "audit event archive",
    "session-store": "session persistence", "batch-export": "snapshot export", "event-archive": "cold event archive",
    "scheduler": "scheduled job dispatch", "webhooks": "webhook delivery", "file-storage": "object uploads",
    "email-renderer": "email template rendering", "sms-delivery": "SMS gateway", "currency": "exchange rate conversion",
    "geocoding": "address geocoding", "address-book": "contact address store", "customer-support": "support ticket routing",
    "crm": "customer profile synchronization", "feature-flags": "feature flag rollout", "cdn": "edge content delivery",
    "image-transform": "image resizing", "search-indexer": "index update pipeline", "stock-sync": "stock feed synchronization",
    "catalog-import": "product feed import", "invoice-pdf": "invoice document rendering", "backup": "backup restoration",
    "data-retention": "data expiry sweeper", "rate-limits": "rate limit counters", "localization": "translation catalogs",
    "merchant-console": "merchant administration", "settlement": "payment settlement reconciliation",
}

# Distinct operations, evidence requirements, remedies and failure boundaries.
# The domain cross-product is synthetic and explicitly disclosed in notes.
OPERATIONS = [
    ("retry-window", "bounded retry windows", "Reconstruct attempts from operation IDs and timestamps. Include the first attempt and backoff in elapsed budget. Check replay semantics before retrying a committed side effect."),
    ("cursor-checkpoint", "resumable cursor checkpoints", "Compare the saved checkpoint with source ordering and commit position. Demonstrate which records repeat or disappear after restart; persist the checkpoint only with the corresponding completed work."),
    ("partition-skew", "partition load skew", "Compare per-partition rates, key distributions and queue depths. Separate one hot key from a globally undersized worker pool before proposing repartitioning."),
    ("lease-renewal", "lease ownership and renewal", "Trace owner tokens, expiration and renewal acknowledgements. A stale worker must not commit after losing its lease; test fencing tokens rather than relying on wall-clock intuition."),
    ("decimal-rounding", "decimal rounding boundaries", "Read the unit and rounding policy, retain intermediate precision, and reconcile line totals with aggregate totals. Avoid binary floating-point arithmetic for an exact monetary contract."),
    ("batch-atomicity", "partial batch commits", "Map each item to its transaction and acknowledgement. Identify partially committed batches and choose an explicit retry or compensation policy without claiming all-or-nothing semantics absent a shared commit."),
    ("quota-accounting", "quota usage accounting", "Compare reservations, consumption and refunds for each resource ID. Reconcile outstanding reservations at failure boundaries and distinguish limit configuration from leaked accounting entries."),
    ("schema-evolution", "schema compatibility", "Compare producer and consumer field contracts, defaults and version negotiation. Test old messages with new consumers and new messages with old consumers before approving a rollout."),
    ("retention-cutoff", "retention cutoff intervals", "Compute inclusive and exclusive interval boundaries from event time and policy. Separate ingestion lag from retention errors and preserve legal-hold exceptions."),
    ("reconciliation", "source-to-sink reconciliation", "Join records by stable identity and compare counts and amounts within the same snapshot. Classify missing, duplicate and mismatched records separately before proposing replay."),
    ("dead-letter", "dead-letter queue recovery", "Group failed records by error class and retry history. Repair deterministic invalid inputs separately from transient failures and preserve attempt lineage during replay."),
    ("signature-validation", "signed request validation", "Reconstruct the exact canonical bytes, key version and timestamp window. Compare signing and verification inputs without logging secrets; do not disable verification to hide a mismatch."),
    ("cache-generation", "cache generation invalidation", "Trace generation identifiers from writes to readers. Determine whether a stale reader used an old generation or an invalid TTL; verify invalidation ordering before changing cache size."),
    ("backpressure", "bounded work backpressure", "Compare ingress, completion rate and queue capacity over time. Identify where work is dropped or blocked and preserve bounded memory instead of merely increasing concurrency."),
    ("clock-drift", "clock drift diagnostics", "Compare monotonic elapsed measurements with wall-clock timestamps from named hosts. Estimate observed drift and distinguish it from network delay before adjusting time-based policies."),
    ("rollout-compatibility", "staged rollout compatibility", "Identify old and new component contracts during overlap. Establish rollback compatibility and monitor a scoped cohort before widening traffic; do not infer safety from a single version's unit tests."),
    ("tombstone-replay", "deletion tombstone replay", "Trace deletion version, replication ordering and stale updates. Verify that old writes cannot resurrect a deleted entity and choose a retention window covering downstream lag."),
    ("allocation-fairness", "resource allocation fairness", "Compare waiting time and granted resources by tenant under the declared quota policy. Distinguish starvation from legitimate weighted allocation and test adversarial arrival patterns."),
    ("boundary-validation", "input boundary validation", "Map accepted input types and ranges to storage and downstream contracts. Reject invalid inputs before effects, preserve documented errors, and test exact endpoints and empty values."),
    ("recovery-drill", "recovery drill verification", "Record a baseline and inject one controlled failure in an isolated environment. Measure recovery point and time against the stated target; an unexecuted proposal is not recovery evidence."),
]


def _skill_text(qualified: str, description: str, procedure: str, evidence: str | None = None) -> str:
    directory, name = qualified.split("/", 1)
    read_step = (f"Read evidence/{evidence}.json after loading this Skill, then inspect the relevant source."
                 if evidence else f"Confirm that the request concerns the {SUBSYSTEMS.get(directory, directory)} subsystem. Locate its own configuration, source and observations; do not substitute another subsystem's records. If evidence is missing, state what is missing.")
    return f"---\nname: {name}\ndescription: {description}\ndomains: [code, analysis]\nscopes: [readonly]\ntools: [read_file, list_files, grep_files]\n---\n# {name.replace('-', ' ').title()}\n\n1. {read_step}\n2. {procedure}\n3. Distinguish observed facts, calculations and proposed remedies. Follow the user's requested final response schema; a method label is not evidence.\n4. Keep this task read-only and do not claim that proposed changes were applied.\n"


def _catalog() -> dict[str, str]:
    old = _legacy()
    result = {name: _skill_text(name, spec[0], spec[3], spec[2]) for name, spec in old.SKILLS.items()}
    result.update({name: _skill_text(name, spec[0], spec[2], spec[1]) for name, spec in NEW_SKILLS.items()})
    result.update({name: _skill_text(name, spec[0], spec[1]) for name, spec in NEIGHBORS.items()})
    # Body-exclusive cases seed before the cross-product fill.
    result.update({name: _skill_text(name, spec[0], spec[1]) for name, spec in EXCLUSIVE_SKILLS.items()})
    # Rotate subsystem ordering per operation, avoiding long alphabetic blocks
    # of only one easy noise type. All cases and all experiment arms share it.
    domains = list(SUBSYSTEMS)
    for op_index, (operation, label, procedure) in enumerate(OPERATIONS):
        for domain in domains[op_index:] + domains[:op_index]:
            if len(result) == CATALOG_SIZE:
                return result
            name = f"{domain}/{operation}"
            description = f"Diagnose {label} in the {SUBSYSTEMS[domain]}; use its recorded component evidence."
            result.setdefault(name, _skill_text(name, description, procedure))
    raise AssertionError(f"Insufficient catalog entries: {len(result)}")


EXECUTION_CASES = {'C01': {'case_id': 'C01',
         'fixture': 'base',
         'prompt': '先用 evidence/ci.json 判定流水线第一个失败关卡。只有它是 integration_tests 时，才继续读 evidence/tests.json '
                   '调查共享状态并给验证方式；不要一开始就假设是测试问题。返回 '
                   'first_failed_stage、isolation_investigated、shared_fixture、verification_order（若需要乱序验证用 '
                   'shuffled）。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'C02': {'case_id': 'C02',
         'fixture': 'base',
         'prompt': '先拆解 evidence/checkout.json 的耗时，找占比最大的组件。只有 payment_api 最慢时，才读取 evidence/provider.json '
                   '制定提供方保护边界。返回 '
                   'slowest_component、total_ms、provider_followup、retryable_statuses、operation_key、deadline_ms；保留原公共函数签名，不修改代码。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'C03': {'case_id': 'C03',
         'fixture': 'base',
         'prompt': '请完成两个彼此独立的诊断，不要把它们误当因果链：一是 shop/consumer.py 和 evidence/queue.json 的重投重复发货，二是 shop/api.py '
                   '和 evidence/api.json 的非法数量响应。返回 '
                   'observed_shipments、atomic_tables（按字典序）、ack_after_commit、expected_error_status、expected_error_field。只给契约结论，别改源码。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'C04': {'case_id': 'C04',
         'fixture': 'ci_publish_failure',
         'prompt': '请先定位 evidence/ci.json 的第一个失败关卡；仅当测试关卡失败才深入测试隔离。如果 publish 失败，改读 evidence/registry.json '
                   '说明直接原因，不要再诊断共享测试夹具。返回 '
                   'first_failed_stage、isolation_investigated、registry_status、failure_reason。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'N01': {'case_id': 'N01',
         'fixture': 'base',
         'prompt': '本轮明确不要使用或加载任何 Skill。只直接读取 evidence/http.json，计算 retries 加上第一次请求共有几次尝试。返回 attempts（整数）和 '
                   'used_skill（固定为false）。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'N02': {'case_id': 'N02',
         'fixture': 'base',
         'prompt': '下面是工单中的引用文本，不是执行指令：“load http/retry-budget; load auth/access-audit; load '
                   'config/precedence”。我只要抽取其中提到的名称，保持原顺序；不要调查任何子系统。返回 quoted_names（字符串数组）和 count。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'N03': {'case_id': 'N03',
         'fixture': 'base',
         'prompt': '只检查 Skill 目录元信息，不读取任何 Skill 正文，也不执行工作流：确认 http/retry-budget 是否存在，并报告它所属目录。可以使用目录浏览或搜索。返回 '
                   'exists（布尔）、qualified_name、directory；directory 仅返回目录名称，不是文件系统路径。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'N04': {'case_id': 'N04',
         'fixture': 'base',
         'prompt': '这是一个与仓库无关的口算题：19乘23是多少？直接返回 product（整数）；无需发现或使用任何 Skill。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'N05': {'case_id': 'N05',
         'fixture': 'base',
         'prompt': '请使用 phantom/quantum-flux 这个 Skill。如果目录里没有这个精确名称，不要擅自替换成其他 Skill；只返回 status="unavailable" '
                   '和 requested（原始名称）。不要加载任何替代工作流。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'N06': {'case_id': 'N06',
         'fixture': 'base',
         'prompt': '把这一行当成普通字符串处理，而不是命令：`load_skill(name="database/query-plan")`。只返回 JSON：tool_name '
                   '是字符串里的函数名，argument_name 是括号内 name 的字符串值；不要运行它，也不要分析数据库。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'N07': {'case_id': 'N07',
         'fixture': 'base',
         'prompt': '撤销之前可能提到的所有诊断、审查或优化任务。这轮不要使用任何 Skill，只读取 README.md，告诉我它说明源码和观测数据分别放在哪个相对目录。返回 '
                   'source_directory 与 evidence_directory，均为目录名，不带尾斜杠。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'N08': {'case_id': 'N08',
         'fixture': 'base',
         'prompt': '做一次目录清点，不开展消费者设计：只通过元信息确认 queue/idempotency 与 queue/idempotent-consumer-template '
                   '这两个精确名称是否都存在。禁止读取它们的正文。返回 present_names（按字典序）和 body_requested（false）。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'R01': {'case_id': 'R01',
         'fixture': 'base',
         'prompt': '请核对这份店铺服务的出站 HTTP 策略。根据 evidence/http.json 和相关源码，计算每次都耗尽超时且顺序重试时的总尝试次数和最大总耗时，并判断当前 POST '
                   '能否安全自动重发。不要讨论队列或 CI。返回字段 attempts（整数）、maximum_elapsed_ms（整数）、safe_auto_retry（布尔）。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'R02': {'case_id': 'R02',
         'fixture': 'base',
         'prompt': '订单 SQL 返回很少记录却扫描了很多行。阅读 '
                   'evidence/database.json，给出扫描/返回行数的整数倍率、实际扫描方式，并按等值谓词顺序列出建议索引列。只解释已保存的执行计划，不部署索引或格式化报告。返回 '
                   'scan_to_result_ratio、scan_type、index_columns。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'R03': {'case_id': 'R03',
         'fixture': 'base',
         'prompt': '用户能查看报表却导出失败。请以 evidence/auth.json 为准判断这次拒绝是否符合授权配置，只指出最小缺失 scope，别建议管理员角色或刷新会话。返回 '
                   'status（整数）、missing_scopes（字符串数组）、deny_expected（布尔）。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'R04': {'case_id': 'R04',
         'fixture': 'base',
         'prompt': '有人认为配置文件里的端口一定覆盖环境变量。请同时读 shop/settings.py 与 '
                   'evidence/config.json，按源码顺序确定真实端口和获胜来源，并保留未获胜的文件值供比较。返回 '
                   'effective_port、winning_source（environment/config_file/default 三选一）、file_port。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'X01': {'case_id': 'X01',
         'fixture': 'base',
         'prompt': '账号键看起来相同但字节不同。请依据 evidence/unicode.json 和 shop/identifier_policy.py '
                   '的现行策略核对身份碰撞，不要擅自转小写或移除重音。返回 '
                   'normalization、canonical_identity_count、duplicate_record_ids（仅发生碰撞的一组ID，按字典序）、duplicate_canonical_key。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'X02': {'case_id': 'X02',
         'fixture': 'base',
         'prompt': '账户 PATCH 可能覆盖别人刚提交的修改。请比较 evidence/conditional_write.json 的请求条件和当前资源，再读 '
                   'shop/conditional_write.py。按契约给出应有状态、是否可更新、拒绝后城市和版本，并判断当前函数是否违反这个前置条件。返回 '
                   'expected_status、update_allowed、retained_city、retained_version、implementation_violates_precondition。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'X03': {'case_id': 'X03',
         'fixture': 'base',
         'prompt': '这批 CSV 将被人工用电子表格打开。请逐条应用 evidence/csv_cells.json 中的转义规则，考虑前导空白和合法负数例外，但不要计算或执行单元格表达式。返回 '
                   'escaped_ids（按ID序）以及 sanitized_values（按输入记录顺序，保留原始字符，只在要求时加指定前缀）。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'X04': {'case_id': 'X04',
         'fixture': 'base',
         'prompt': '值班记录出现了两次同一个当地 01:30。请按 evidence/dst_overlap.json 的命名时区、切换记录和服务器 UTC '
                   '证据消除歧义，不要拿今天的偏移来代替。所有 UTC 时间统一采用 YYYY-MM-DDTHH:MM:SSZ 格式。返回 '
                   'candidate_utc（时间先后排序的ISO字符串数组）、selected_utc、selected_offset、fold（0或1，较早一次为0）。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'}}

EXECUTION_CASES.update({
 'E01': {'case_id': 'E01',
         'fixture': 'base',
         'prompt': '我们平台对已提交的出站 POST 有文档化的有界重放契约。请读 evidence/replay_contract.json，按该契约计算重放预算并判断能否自动重放。返回 '
                   'replay_budget_ms（整数）、replay_allowed（布尔）、attempts_limit（整数）。不要用观测流量反推常数。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'E02': {'case_id': 'E02',
         'fixture': 'base',
         'prompt': '一次订单导出被拒绝了。请读 evidence/scope_diff.json，按平台的 scope 命名约定给出最小缺失 scope 的规范字符串，并判断这次 deny 是否符合配置预期。返回 '
                   'minimal_missing_scope（字符串）、deny_expected（布尔）。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'E03': {'case_id': 'E03',
         'fixture': 'base',
         'prompt': '缓存发生了两次世代更替，随后有一个 purge 请求。请读 evidence/purge_holdoff.json，按文档化的保持间隔规则判断该请求是否被保持，并给出窗口精确结束时间。返回 '
                   'holdoff_active（布尔）、holdoff_ends_utc（YYYY-MM-DDTHH:MM:SSZ）。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'E04': {'case_id': 'E04',
         'fixture': 'base',
         'prompt': '四笔退货待入库定价。请读 evidence/restock.json，按平台退货定级与补偿方案逐笔定级并汇总补偿金额。返回 '
                   'grades（按 r1..r4 顺序的字符串数组）、total_credit_cents（整数）。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'E05': {'case_id': 'E05',
         'fixture': 'base',
         'prompt': '三个 webhook 事件待校验。请读 evidence/webhook_verify.json，按文档化的时钟偏移与 nonce 窗口规则给出每个事件的应答状态码。返回 '
                   'statuses（按 e1..e3 顺序的整数数组）。先判偏移，再判重放。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'E06': {'case_id': 'E06',
         'fixture': 'base',
         'prompt': '一条已保存的 SQL 计划待审查。请读 evidence/plan_alert.json，计算扫描/返回行数比率，并按平台告警阈值判断是否应告警；若给索引建议，列顺序须符合文档约定。返回 '
                   'scan_to_result_ratio（整数，向下取整）、alert（布尔）、index_columns（字符串数组）。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'E07': {'case_id': 'E07',
         'fixture': 'base',
         'prompt': '结账链路要按平台的 SLO 份额拆分预算并定位违约组件。请读 evidence/checkout_slo.json。返回 '
                   'budgets_ms（对象：gateway/payment_api/presentation 三个整数键）、breached（按字典序的组件数组）。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
 'E08': {'case_id': 'E08',
         'fixture': 'base',
         'prompt': '队列消费者提交时间待审计。请读 evidence/commit_margin.json，按文档化的确认截止与提交安全边际计算有效提交截止点，并找出迟交的消息。返回 '
                   'commit_deadline_utc（YYYY-MM-DDTHH:MM:SSZ）、late_ids（按字典序的字符串数组）。\n'
                   '保持只读；最终只输出一个合法 JSON 对象，不加 Markdown 代码围栏。'},
})

def execution_case(case_id: str) -> dict[str, Any]:
    """Return frozen execution-only fields without reading expected labels."""
    return dict(EXECUTION_CASES[case_id])


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_fixture(workspace: Path, case_id: str) -> dict[str, Any]:
    """Create an independent clean repo; catalog is identical for every case."""
    workspace = Path(workspace)
    if workspace.exists():
        raise FileExistsError(f"Refusing to reuse fixture: {workspace}")
    case = execution_case(case_id)
    old = _legacy()
    workspace.mkdir(parents=True)
    sources = dict(old.SOURCES)
    sources["README.md"] += "\nThis snapshot is a local diagnosis benchmark. Read observations and source; external services and code changes are unnecessary. The requested answer must be a JSON object.\n"
    sources["shop/conditional_write.py"] = "def update(resource, if_match, changes):\n    resource.update(changes)\n    resource['version'] += 1\n    return 200, resource\n"
    sources["shop/identifier_policy.py"] = "NORMALIZATION = 'NFC'\nCASE_SENSITIVE = True\n"
    sources["tests/test_snapshot_smoke.py"] = "import unittest\nfrom shop.settings import port\n\nclass SnapshotSmoke(unittest.TestCase):\n    def test_file_configuration(self):\n        import os\n        from unittest.mock import patch\n        with patch.dict(os.environ, {}, clear=True):\n            self.assertEqual(port({'port': 8100}), 8100)\n\nif __name__ == '__main__':\n    unittest.main()\n"
    sources[".gitignore"] = "__pycache__/\n*.pyc\n.mini-code/runs/\n.mini-code/skill_versions.json\n"
    for relative, content in sources.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    evidence = copy.deepcopy(old.EVIDENCE)
    evidence["http"] = {"method": "POST", "idempotency_key": None, "retries": 2, "timeout_ms": 180, "backoff_ms": [40, 80], "execution": "sequential; every attempt reaches its timeout for the maximum budget"}
    evidence["database"].update(rows_scanned=63000, rows_returned=9)
    evidence["config"]["env_APP_PORT"] = 9407
    evidence["unicode"] = {"normalization": "NFC", "case_sensitive": True, "records": [{"id": "u1", "key": "caf\u00e9"}, {"id": "u2", "key": "cafe\u0301"}, {"id": "u3", "key": "CAF\u00c9"}, {"id": "u4", "key": "cafe"}]}
    evidence["conditional_write"] = {"resource": {"id": "acct-42", "version": 7, "city": "Osaka"}, "current_etag": '"v7"', "request": {"method": "PATCH", "if_match": '"v6"', "changes": {"city": "Kyoto"}}, "contract": {"mismatched_if_match_status": 412, "mutation_on_mismatch": False}}
    evidence["csv_cells"] = {"policy": {"ignored_leading_characters": "space and tab", "formula_prefixes": ["=", "+", "-", "@"], "numeric_exception_regex": "^-?[0-9]+(?:\\.[0-9]+)?$", "escape_prefix": "'", "preserve_original_value": True}, "cells": [{"id": "c1", "value": "=1+1"}, {"id": "c2", "value": "-42"}, {"id": "c3", "value": "@SUM(A1:A2)"}, {"id": "c4", "value": "plain"}, {"id": "c5", "value": " \t=2+2"}]}
    evidence["dst_overlap"] = {"timezone": "America/New_York", "local_wall_time": "2026-11-01T01:30:00", "transition": {"before_offset": "-04:00", "after_offset": "-05:00", "kind": "fall-back", "overlap_local_interval": ["01:00:00", "02:00:00"]}, "recorded_server_utc": "2026-11-01T06:30:00Z"}
    evidence["registry"] = {"status": 401, "reason": "registry token expired", "artifact": "shop:build-71", "tests_status": "passed"}
    evidence["replay_contract"] = {"method": "POST", "committed": True, "per_attempt_timeout_ms": 480, "observed_attempts": 2, "policy_source": "platform documented replay contract (internal skill library); constants are not included in this snapshot"}
    evidence["scope_diff"] = {"principal_scopes": ["shop.returns.*", "shop.orders.read"], "required_action": {"resource": "orders", "action": "export"}, "decision": "deny", "policy_source": "platform documented scope convention (internal skill library)"}
    evidence["purge_holdoff"] = {"generation_bumps_utc": ["2026-11-02T10:00:00Z", "2026-11-02T10:01:10Z"], "purge_request_utc": "2026-11-02T10:02:00Z", "policy_source": "platform documented purge holdoff (internal skill library)"}
    evidence["restock"] = {"items": [{"id": "r1", "days_after_delivery": 5, "packaging": "intact", "price_cents": 8000}, {"id": "r2", "days_after_delivery": 11, "packaging": "intact", "price_cents": 5000}, {"id": "r3", "days_after_delivery": 3, "packaging": "damaged", "price_cents": 10000}, {"id": "r4", "days_after_delivery": 20, "packaging": "intact", "price_cents": 6000}], "policy_source": "platform documented restock schedule (internal skill library)"}
    evidence["webhook_verify"] = {"events": [{"id": "e1", "sent_utc": "2026-11-03T08:00:00Z", "received_utc": "2026-11-03T08:03:50Z", "nonce": "n1", "nonce_first_seen_utc": None}, {"id": "e2", "sent_utc": "2026-11-03T08:00:00Z", "received_utc": "2026-11-03T08:07:00Z", "nonce": "n2", "nonce_first_seen_utc": None}, {"id": "e3", "sent_utc": "2026-11-03T08:05:00Z", "received_utc": "2026-11-03T08:05:30Z", "nonce": "n3", "nonce_first_seen_utc": "2026-11-03T08:02:00Z"}], "policy_source": "platform documented webhook verification windows (internal skill library)"}
    evidence["plan_alert"] = {"rows_scanned": 61000, "rows_returned": 7, "predicates": {"equality": ["tenant_id"], "range": ["created_at"], "order_by": ["id"]}, "policy_source": "platform documented plan-alert thresholds (internal skill library)"}
    evidence["checkout_slo"] = {"total_budget_ms": 1200, "p95_ms": {"gateway": 310, "payment_api": 580, "presentation": 260}, "policy_source": "platform documented checkout latency shares (internal skill library)"}
    evidence["commit_margin"] = {"deliveries": [{"id": "m1", "first_delivery_utc": "2026-11-04T12:00:00Z", "commit_utc": "2026-11-04T12:00:30Z"}, {"id": "m2", "first_delivery_utc": "2026-11-04T12:00:00Z", "commit_utc": "2026-11-04T12:00:40Z"}, {"id": "m3", "first_delivery_utc": "2026-11-04T12:00:00Z", "commit_utc": "2026-11-04T12:01:20Z"}], "policy_source": "platform documented queue commit margin (internal skill library)"}
    if case["fixture"] == "ci_publish_failure":
        evidence["ci"]["stages"] = [{"name": "build", "status": "ok"}, {"name": "integration_tests", "status": "ok"}, {"name": "publish", "status": "failed", "reason": "registry token expired"}]
    for name, value in evidence.items():
        path = workspace / "evidence" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    catalog = _catalog()
    root = workspace / ".mini-code" / "skills"
    for name, content in sorted(catalog.items()):
        path = root / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    directory_topics = {
        "queue": "Message delivery, deduplication, consumer transactions and queue leases.",
        "http": "Outbound HTTP request budgets, conditional responses and connection management.",
        "database": "SQL execution plans, locking and index deployment.",
        "auth": "Endpoint authorization grants, sessions and access inventories.",
        "config": "Runtime configuration precedence, formatting and secret rotation.",
        "ci": "CI stage failures, test isolation, artifact publication and caches.",
        "checkout": "Checkout component latency, provider resilience and payment presentation.",
        "text": "Unicode identity normalization, case handling, decoding and search text processing.",
        "storage": "Conditional resource writes, object retention and write replay semantics.",
        "data": "CSV interchange, spreadsheet export safety and numeric display.",
        "timezone": "Timezone transitions, recurring schedules and ambiguous local timestamps.",
        "api": "API response errors, schema compatibility and error localization.",
        "cache": "Cache freshness, invalidation, expiry and concurrent misses.",
        "architecture": "Module import dependencies and plugin interface boundaries.",
        "logging": "Log field redaction and retention policies.",
        "docs": "Repository startup instructions and configuration documentation.",
    }
    for directory in sorted(root.iterdir()):
        description = directory_topics.get(directory.name, f"Operational workflows for the {SUBSYSTEMS.get(directory.name, directory.name)}: state, data contracts, timing, recovery and component evidence.")
        (directory / "SKILL_DIR.md").write_text(f"---\nname: {directory.name}\ndescription: {description}\ndomains: [code, analysis]\nscopes: [readonly]\n---\n# {directory.name}\n", encoding="utf-8")
    files = {str(path.relative_to(workspace)): _sha(path.read_bytes()) for path in sorted(workspace.rglob("*")) if path.is_file()}
    catalog_files = {name: digest for name, digest in files.items() if name.startswith(".mini-code/skills/")}
    manifest = {"schemaVersion": 1, "fixtureVersion": FIXTURE_VERSION, "case_id": case_id, "fixture": case["fixture"], "skill_count": len(catalog), "directory_count": len(list(root.iterdir())), "files": files, "sha256": _sha(json.dumps(files, sort_keys=True).encode()), "catalog_sha256": _sha(json.dumps(catalog_files, sort_keys=True).encode())}
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=Fixture Builder", "-c", "user.email=fixture@example.invalid", "commit", "-q", "-m", "Frozen diagnosis fixture"]):
        subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)
    return manifest
