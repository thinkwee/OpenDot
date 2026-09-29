---
name: morning-brief
description: A short daily brief — weather, calendar, top news in the human's interests, anything waiting on them.
tags: [routine, notify]
---

# Morning brief

A proactive, once-a-day summary. Usually triggered by a `schedule` cron automation
(e.g. `0 8 * * *`) rather than run ad hoc — if the human asks for one "every morning",
set that up with `schedule` first.

## What to gather

1. **Today's shape**: day of week, date, anything from MEMORY.md about recurring
   commitments (gym, meetings, deadlines) worth surfacing.
2. **News**: 3–5 headlines relevant to what MEMORY.md / USER.md say the human cares
   about (their industry, hobbies, a company they follow). Use `web_search` for
   "<topic> news today", then `web_fetch` the ones that look substantive. Don't pad
   with generic top-of-Google stories the human never asked about.
3. **Anything waiting on them**: check `list_automations` output isn't needed here, but
   do mention if you (or a teammate) left something in their Inbox recently that they
   haven't acted on, if you know about it from the journal.
4. Keep it SHORT — a phone-readable brief, not an essay. Aim for under 200 words of
   text, or offer a `publish_page` version if there's more to show (charts, several
   news items with summaries).

## Delivery

- If this is a heartbeat/cron run, just reply normally — it lands in the Inbox as a
  report automatically. Don't call `notify` as well (that would duplicate it).
- Open with something warm and specific, not "Good morning! Here is your brief:".
- If there's genuinely nothing new since yesterday, say so briefly rather than
  padding — the human will trust the brief more if it isn't always the same length.
