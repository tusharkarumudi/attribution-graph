# Architecture Decision Records

One record per load-bearing decision, in the standard shape: context, decision,
consequences.

These exist because several of the model's choices look arbitrary until
explained. A reviewer who reads `GROUP_CAP = 8.0` as a magic number will discount
everything downstream of it; ADR 0003 converts that constant into an argument.

| # | Decision |
|---|---|
| [0001](0001-three-layer-data-model.md) | Separate Identifier, Claim and Entity |
| [0002](0002-correlation-groups.md) | Correlation groups as the unit of independence |
| [0003](0003-structural-corroboration.md) | Group cap below the prior |
| [0004](0004-definitional-carve-out.md) | Registry assertions are definitional |
| [0005](0005-drop-vs-demote.md) | Two filter verdicts, not one |
| [0006](0006-measured-selectivity.md) | Selectivity from a corpus, not a weight table |
| [0007](0007-declared-fetch-policy.md) | Fetch policy declared, not enforced |
| [0008](0008-person-scoped-opt-in.md) | Three-key opt-in for person-scoped collection |
| [0009](0009-no-network-io-in-core.md) | No network I/O in the inference core |
| [0010](0010-llm-assertion-ceiling.md) | Reliability ceiling on machine-generated assertions |
