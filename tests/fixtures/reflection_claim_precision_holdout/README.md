# Reflection Claim Precision Holdout

This dataset is synthetic and manually labeled for final persistable claim quality.
It separates primary claims, legal secondary claims, redundant same-chain claims,
and forbidden claims. Response capture is allowed only for these synthetic cases.

The manifest fixes the 15 same-case real A/B sample. Each prompt arm therefore
uses 15 provider requests, split across commands capped at ten requests.
