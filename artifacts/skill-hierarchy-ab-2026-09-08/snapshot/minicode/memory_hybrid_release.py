"""Release trust anchors for promoted Hybrid Memory evidence.

This module is intentionally excluded from the semantic implementation freeze:
promotion changes only these reviewed evidence fingerprints after the frozen
implementation has passed its one-shot gates.
"""

HYBRID_ACCEPTED_PROMOTION_FINGERPRINT = (
    "b49e3261bc6cf81435de87fc503a4197713a55d3d4070fa57122b74772fdc872"
)
HYBRID_ACCEPTED_QWEN_PROMOTION_FINGERPRINT = (
    "bd317c42adb2d9d21807add9030ef111a197b9817bb88df6f24651600397e61e"
)
# Populated only after the preregistered resolver-pressure one-shot passes.
# An empty value deliberately blocks the independent promotion holdout.
HYBRID_ACCEPTED_RESOLVER_PRESSURE_REPORT_FINGERPRINT = (
    "b727d2dd61d4fdf78289553fbfc4ef9c0c4fe83789af1bbead9e24ef4c404fcf"
)
