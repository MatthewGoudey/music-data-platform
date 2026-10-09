# ADR 0008: Venue travel times

- Status: Accepted (recommendation; change by editing this file)
- Date: 2026-10-07
- Decision: Seed only

The 268 known venues load with their computed travel times from `seeds/venues.csv`. New venues get times by hand or from an optional job that cannot fail the nightly sync. The Google Routes dependency that broke the old sync is gone.

## Amendment (2026-10-09): times from the Routes API, by CTA and on foot

Matt asked for travel times now, by public transport and on foot, from his home (kept in
`HOME_ADDRESS`, in the env files and secrets, never in the repo). `musicdata venues travel`
asks the Google Routes API (`computeRouteMatrix`) for a transit time (a Friday 6:30 PM
departure; it includes the walks to and from the stops) and a walking time for each venue
without times, venues with upcoming shows first (migration 0016). The nightly sync times
new venues in an optional step that cannot fail the sync. The page shows the sensible way
to go: a walk of 20 minutes or less alone, a walk up to 40 minutes beside the CTA time,
otherwise the CTA time. The old pipeline's driving times stay unused.
