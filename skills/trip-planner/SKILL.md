---
name: trip-planner
description: Plans a trip end to end — flights/trains, a place to stay, a day-by-day itinerary — and turns it into a page.
tags: [research, delegate, pages]
---

# Trip planner

Triggered by things like "plan a long weekend in Porto" or "help me plan our trip to Vietnam
in March".

## Steps

0. `read_skill('find-it-online')` (and pass it on to helpers): where to look up
   flights, stays and getting around so the first try works.

1. **Clarify budget-affecting unknowns only if truly needed** (dates, number of people,
   rough budget) — but if the human already gave enough, don't interrogate them; make
   sensible assumptions and say what you assumed.
2. **Fan out with `delegate`** into independent tracks, e.g.:
   - transport options (flights/trains) with rough prices
   - 2–3 accommodation options with a link and price range
   - things to do, grouped by neighbourhood/day, with one "don't miss" pick
   - practical notes: visa/entry requirements, typical weather for the dates, local
     transit basics
3. Merge into a **day-by-day itinerary** — morning/afternoon/evening, walking-distance
   grouped, with realistic timing (don't cram 8 museums into one afternoon).
4. `publish_page` it as a simple itinerary page (day headers, times, one line per
   activity, a packing/notes section at the bottom) — this is what the human will
   actually open on their phone while traveling. Keep the HTML self-contained and
   readable at phone width.
5. Optionally `remember` firm preferences that came up (e.g. "prefers boutique hotels
   over chains") for next time.

## Rules

- Give prices as rough ranges, not invented exact figures — say "around €80–120/night".
- Always include booking links where you found real ones; never fabricate a URL.
- If the trip depends on something time-sensitive (visa processing, event tickets),
  call that out prominently near the top, not buried in day 4.
