# ADR 0004: Derived layer

- Status: Proposed
- Date: 2026-10-07
- Decision: Plain SQL first, dbt optional later

Sessions, stats and views are computed by the `derive` job with plain SQL files. dbt-core can be adopted in Phase 2 without changing the schema if having dbt on the resume is worth the second tool. Decide when Phase 2 starts.
