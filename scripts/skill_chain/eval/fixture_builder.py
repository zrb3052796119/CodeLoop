"""Deterministic read-only mini-project and substantive synthetic Skills.

The fixture contains evidence, not expected labels. No provider is imported.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SKILLS = {
    "queue/idempotency": ("Design idempotent message consumers and prevent duplicate shipment side effects.", "Delivery boundary", "queue", "Explain why processed-message insertion and shipment_outbox insertion must share one transaction; acknowledge only after commit."),
    "queue/idempotent-consumer-template": ("Use a processed-message table plus transactional outbox for idempotent queue consumers.", "Delivery boundary", "queue", "Explain why processed-message insertion and shipment_outbox insertion must share one transaction; acknowledge only after commit."),
    "http/retry-budget": ("Diagnose HTTP request retries and choose safe bounded retry and timeout settings.", "Retry budget", "http", "Compute total attempts including the first request. Explain why a non-idempotent POST cannot be retried automatically without an idempotency key."),
    "database/query-plan": ("Explain a slow SQL query using its execution plan and index columns.", "Plan evidence", "database", "Compare rows scanned with rows returned; identify the predicate and propose the matching composite index without changing SQL results."),
    "cache/expiry-analysis": ("Diagnose stale cached prices by comparing TTL and invalidation events.", "Freshness boundary", "cache", "Compare the event sequence with the TTL. Explain the stale interval and the missing invalidation event."),
    "ci/pipeline-diagnosis": ("Locate a failing deployment pipeline stage from recorded job outcomes before suggesting changes.", "Stage evidence", "ci", "Name the first failed stage from the job log. Only if it is integration_tests, then load ci/test-isolation and read its evidence before recommending a test strategy; otherwise explain the actual stage failure and do not assume tests failed."),
    "ci/test-isolation": ("Diagnose order-dependent integration tests and shared mutable test fixtures in a CI pipeline.", "Isolation boundary", "tests", "Explain the shared fixture leak and recommend a fresh fixture per test plus shuffled-order verification; preserve pipeline stage names and order."),
    "auth/access-audit": ("Explain an authorization failure by comparing user role grants and the endpoint's required scope.", "Access decision", "auth", "Compare the exact required scope with the caller grants; recommend the smallest grant change, never an administrator bypass."),
    "config/precedence": ("Explain which configuration source wins when defaults, config files, and environment values differ.", "Effective configuration", "config", "Trace precedence in the source code, then report the effective value and the source that supplied it."),
    "architecture/import-cycle": ("Analyze Python import cycles and explain a minimal dependency direction repair.", "Dependency seam", "architecture", "Draw the observed import cycle in prose and move the shared constants to a leaf module rather than adding deferred imports everywhere."),
    "logging/redaction": ("Review application logs for credential disclosure and propose field-level redaction.", "Redaction boundary", "logging", "Identify the unsafe field without reproducing its value; preserve request_id and error_code for debugging."),
    "api/error-contract": ("Compare an API error implementation with the documented status and JSON response contract.", "Contract difference", "api", "State expected versus actual status and JSON error field; distinguish a server bug from a client input error."),
    "docs/startup-guide": ("Explain how to start a Python application using its repository entry point and configuration.", "Startup path", "startup", "Report the module entry point, required environment variable, and health route from the source evidence; do not invent commands."),
    "checkout/latency-diagnosis": ("Diagnose checkout latency from component timings before selecting a component-specific remedy.", "Latency breakdown", "checkout", "Compute the largest component from the trace. Only if payment_api is the largest component, then load checkout/provider-resilience and use its evidence. Otherwise explain the actual bottleneck and do not prescribe payment resilience as the cause fix."),
    "checkout/provider-resilience": ("Plan checkout payment-provider timeout and retry resilience after traces confirm slow payment API calls.", "Provider guard", "provider", "Use the provider's retryable status list and operation key requirement. Preserve the public checkout signature and explain a bounded timeout and retry plan."),
}

EVIDENCE = {
    "queue": {"event": "msg-732 delivered twice", "current": "shipment is created before processed-message commit", "tables": ["processing_ledger", "shipment_outbox"], "shipment_count": 2},
    "http": {"method": "POST", "idempotency_key": None, "retries": 3, "timeout_ms": 250, "maximum_attempts": 4},
    "database": {"query": "SELECT * FROM orders WHERE tenant_id=7 AND status='pending'", "plan": "Seq Scan", "rows_scanned": 48000, "rows_returned": 12, "index_columns": ["tenant_id", "status"]},
    "cache": {"price_before": 17, "price_after": 21, "ttl_seconds": 600, "events": ["12:00 cache fill", "12:02 price changed", "12:10 TTL expiry"], "missing_event": "price_changed invalidation"},
    "ci": {"stages": [{"name": "build", "status": "ok"}, {"name": "integration_tests", "status": "failed", "reason": "test_order leak"}, {"name": "publish", "status": "skipped"}]},
    "tests": {"shared_fixture": "GLOBAL_CART", "isolated_result": "pass", "after_test_add_item": "expected 0 items, got 1", "verification": "shuffled order"},
    "auth": {"endpoint": "/reports/export", "required_scope": "reports:export", "caller_scopes": ["reports:read"], "status": 403},
    "config": {"default": 8080, "config_file": 8100, "env_APP_PORT": 9200, "precedence": "environment > config_file > default"},
    "architecture": {"imports": ["orders -> pricing", "pricing -> orders"], "shared_symbol": "TAX_RATE", "suggested_leaf": "constants.py"},
    "logging": {"record_fields": ["request_id", "authorization", "error_code"], "credential_value": "SYNTHETIC-CREDENTIAL-DO-NOT-REPEAT", "safe_fields": ["request_id", "error_code"]},
    "api": {"invalid_quantity": -1, "expected_status": 400, "actual_status": 500, "expected_field": "error_code", "actual_field": "message"},
    "startup": {"module": "shop.app", "required_env": "SHOP_REGION", "health_route": "/healthz"},
    "checkout": {"timings_ms": {"cart_db": 30, "payment_api": 840, "render": 10}, "public_signature": "checkout(cart_id, customer_id)"},
    "provider": {"retryable_status": [429, 503], "operation_key": "order_id", "deadline_ms": 1200, "public_signature": "checkout(cart_id, customer_id)"},
}

SOURCES = {
    "shop/app.py": "import os\nREGION = os.environ['SHOP_REGION']\nHEALTH_ROUTE = '/healthz'\ndef main():\n    return REGION, HEALTH_ROUTE\nif __name__ == '__main__':\n    main()\n",
    "shop/settings.py": "import os\ndef port(config):\n    return int(os.getenv('APP_PORT', config.get('port', 8080)))\n",
    "shop/orders.py": "from .pricing import price\nTAX_RATE = 0.17\ndef total(cart):\n    return price(cart)\n",
    "shop/pricing.py": "from .orders import TAX_RATE\ndef price(cart):\n    return sum(cart) * (1 + TAX_RATE)\n",
    "shop/checkout.py": "def checkout(cart_id, customer_id):\n    return {'cart_id': cart_id, 'customer_id': customer_id}\n",
    "shop/consumer.py": "def consume(message, db):\n    db.ship(message['order_id'])\n    db.mark_processed(message['id'])\n    db.commit()\n",
    "shop/api.py": "def quantity_response(quantity):\n    if quantity < 0:\n        return 500, {'message': 'invalid quantity'}\n    return 200, {'quantity': quantity}\n",
    "README.md": "# Sample shop\nA small Python shop with queue delivery, checkout, auth and CI diagnostics.\nSource is in shop/. Recorded observations are in evidence/*.json.\nThese are saved local observations; external services are unnecessary.\nInspect files and answer with evidence. No code changes are required.\n",
}


def skill_text(qualified_name: str, spec: tuple[str, str, str, str]) -> str:
    description, heading, evidence, procedure = spec
    name = qualified_name.split('/')[-1]
    domain = qualified_name.split('/')[0]
    return f"""---
name: {name}
description: {description}
domains: [{domain}, code]
scopes: [readonly]
tools: [read_file, list_files, grep_files]
---
# {heading}

This workflow is for the {domain} subsystem of the sample shop. Use it only
for that subsystem and the task's current stage.

1. Read evidence/{evidence}.json with read_file after loading this workflow.
2. {procedure}
3. In the final answer use the exact report label "{heading}". Include
   the observed field names or values that justify the conclusion; this
   label identifies the report method, not a substitute for evidence.
4. Keep the task read-only. Never claim that proposed changes were applied.
"""


def build_fixture(workspace: Path, fixture: str = "base", noise: int = 0) -> dict:
    """Build a fresh workspace; noise is additional Skills across 50 directories."""
    if workspace.exists():
        raise FileExistsError(f"Refusing to reuse fixture: {workspace}")
    workspace.mkdir(parents=True)
    for relative, content in SOURCES.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    evidence = json.loads(json.dumps(EVIDENCE))
    if fixture == "checkout_database":
        evidence['checkout']['timings_ms'] = {'cart_db': 910, 'payment_api': 35, 'render': 10}
    if fixture == "ci_registry":
        evidence['ci']['stages'] = [{'name': 'build', 'status': 'ok'}, {'name': 'integration_tests', 'status': 'ok'}, {'name': 'publish', 'status': 'failed', 'reason': 'registry authentication expired'}]
    for key, value in evidence.items():
        path = workspace / "evidence" / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for name, spec in SKILLS.items():
        path = workspace / ".mini-code" / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(skill_text(name, spec), encoding="utf-8")
    noise_topics = ['retry-budget', 'query-plan', 'expiry-analysis', 'pipeline-diagnosis', 'access-audit', 'precedence', 'error-contract', 'latency-diagnosis', 'idempotency', 'idempotent-consumer-template', 'test-isolation', 'import-cycle', 'redaction', 'startup-guide', 'provider-resilience', 'connection-pool', 'timeout-budget', 'rollback-plan', 'health-check', 'event-order']
    noise_domains = ['billing', 'warehouse', 'search', 'tax', 'shipping', 'fraud', 'catalog', 'recommendations', 'notifications', 'returns', 'subscriptions', 'loyalty', 'analytics', 'experiments', 'marketing', 'identity-sync', 'media', 'inventory', 'reporting', 'fulfilment', 'pricing-rules', 'promotions', 'vendor-import', 'audit-trail', 'session-store', 'batch-export', 'event-archive', 'scheduler', 'webhooks', 'file-storage', 'email-renderer', 'sms-delivery', 'currency', 'geocoding', 'address-book', 'customer-support', 'crm', 'feature-flags', 'cdn', 'image-transform', 'search-indexer', 'stock-sync', 'catalog-import', 'invoice-pdf', 'backup', 'data-retention', 'rate-limits', 'localization', 'merchant-console', 'settlement']
    nearby = [('queue', 'idempotency-schema-migration', 'Migrate the schema of an existing idempotency ledger, without designing consumer transaction or redelivery behavior'), ('http', 'retry-metrics-export', 'Export retry counters to monitoring dashboards, without deciding request retry safety'), ('database', 'query-plan-serialization', 'Serialize an existing SQL execution plan into a report file, without diagnosing query performance'), ('cache', 'expiry-dashboard', 'Display TTL expiry counters on a dashboard, without analyzing cache invalidation correctness'), ('ci', 'test-report-format', 'Format already completed test results as a CI report, without diagnosing failures or fixture isolation'), ('auth', 'access-report-format', 'Format access audit records into a CSV, without changing scope grants'), ('config', 'config-file-format', 'Format configuration files and preserve their values, without resolving runtime precedence'), ('architecture', 'import-graph-render', 'Render an already analyzed import graph, without deciding module boundaries'), ('logging', 'log-rotation', 'Rotate application log files by age and size, without inspecting credential disclosure'), ('api', 'error-contract-translation', 'Translate API contract prose, without comparing response implementations'), ('docs', 'startup-logo', 'Prepare a logo caption for an application startup page, without explaining its entry point'), ('checkout', 'payment-dashboard', 'Display saved payment counters, without diagnosing checkout latency or changing resilience')]
    for index in range(noise):
        domain, topic = noise_domains[index % 50], noise_topics[(index // 50) % len(noise_topics)]
        description = f"Diagnose {topic.replace('-', ' ')} for the {domain} subsystem; compare its component configuration, event records and observed failures."
        if index < len(nearby):
            domain, topic, description = nearby[index]
        name = f"{domain}/{topic}"
        path = workspace / ".mini-code" / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: {topic}\ndescription: {description}\ndomains: [{domain}]\nscopes: [readonly]\n---\n# {domain}: {topic}\nFollow this scope: {description}. First verify that the task concerns this component and operation; another component's measurements do not establish its behavior.\n", encoding="utf-8")
    for directory in (workspace / '.mini-code/skills').iterdir():
        descriptions = [spec[0] for name, spec in SKILLS.items() if name.split('/')[0] == directory.name]
        description = ' '.join(descriptions) if descriptions else f'Operational diagnosis for the {directory.name} subsystem: retries, data access, authorization, configuration and component failures.'
        (directory / 'SKILL_DIR.md').write_text(f'---\nname: {directory.name}\ndescription: {description}\ndomains: [{directory.name}]\nscopes: [readonly]\n---\n# {directory.name}\n', encoding='utf-8')
    files = {str(p.relative_to(workspace)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(workspace.rglob('*')) if p.is_file()}
    return {"skill_count": len(SKILLS) + noise, "noise_count": noise, "files": files, "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}
