---
name: childcare-data
description: "Use whenever working on anything that reads, ingests, matches, stores, or presents childcare provider, demographic, transit, or pricing data in Axisence — dataset exploration, ingestion, geocoding, competitor search, research observations, or reports."
---

# Childcare data guardrails

`Docs/Domain/DataSources.md` is the source of truth for the datasets, their columns, their quirks, and the domain vocabulary. Read it first. This skill lists the mistakes that cost the most.

1. No single dataset lists every New York City provider. City day care centers come only from the NYC Health dataset; city family, group family, and school-age programs come only from the NY State dataset. A competitor search that reads one of them misses most of the market.
2. An omitted address or phone number stays omitted. Never geocode, display, infer, or look up a provider whose address the regulator withholds.
3. Licensed capacity is a regulatory maximum, not enrollment and not availability. Availability comes only from observations.
4. Observations are history. Never overwrite a price or waitlist observation; add a new one with its date, method, and researcher.
5. Age groups and schedules are the provider's own definitions. Do not merge "infant" or "full time" across providers without the stated boundaries.
6. Census figures carry their table, vintage, and margin of error. "Children 0–5" states which Census age bracket it uses.
7. Zip codes and ZIP Code Tabulation Areas differ. Join Census data on tabulation areas and say so.
8. A provider vanishing from a source means it is no longer active there. Mark it, do not delete it: old reports and observations still point at it.
9. Contact names, emails, phone numbers, call notes, and research target addresses of real people never go into fixtures, tests, documentation, logs, or client reports.
10. When a fact about a source is learned during work, record it in `Docs/Domain/DataSources.md`, so the next session does not rediscover it.
