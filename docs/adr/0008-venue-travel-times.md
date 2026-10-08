# ADR 0008: Venue travel times

- Status: Accepted (recommendation; change by editing this file)
- Date: 2026-10-07
- Decision: Seed only

The 268 known venues load with their computed travel times from `seeds/venues.csv`. New venues get times by hand or from an optional job that cannot fail the nightly sync. The Google Routes dependency that broke the old sync is gone.
