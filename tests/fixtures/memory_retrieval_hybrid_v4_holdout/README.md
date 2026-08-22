# Hybrid Memory v4 Holdout

Untouched synthetic production-promotion holdout for the Hybrid Memory v4
two-stage admission protocol: concrete-object query gate, relevance verifier,
and deny-by-default challenger.

It was authored and byte-frozen after challenger calibration on spent v2/v3
data and before the first v4 embedding or model run. The 20 cases contain 10
positives and 10 hard negatives; 24 shared backgrounds are fully adjudicated
irrelevant. Thresholds and protocol constants may not change from v4 results.
