---
name: weekly-review
description: Looks back at the week — what got done, what's pending, patterns worth noticing — and sets up the next week.
tags: [routine, memory]
---

# Weekly review

A reflective, once-a-week routine (good as a `schedule` cron, e.g. Sunday evening
or Monday morning). Different from `morning-brief`: this looks *backward* over
the week and helps the human plan, rather than a quick daily snapshot.

## Steps

1. Read your own journal for the past 7 days (`read_file` on
   `journal/<date>.md` for each day, or ask for the recent journal if it's
   already in your context) to see what you actually did for the human this
   week.
2. Check `list_automations` for anything that's been failing silently or
   hasn't fired when it should have — mention it.
3. Summarize in three short sections:
   - **Done** — what got finished (yours or things you know the human did,
     if they mentioned it in chat)
   - **Still open** — loose threads, half-finished asks, things you said
     you'd follow up on
   - **Worth noticing** — a pattern, not a task (e.g. "you've mentioned being
     tired on Mondays three weeks running")
4. Ask (don't assume) whether anything from "still open" should become an
   automation, be dropped, or carried forward.
5. If something durable came out of the week (a decision, a new preference),
   `remember` it so it isn't lost.

## Rules

- Keep it honest — if little happened, say that plainly rather than padding.
- This is a moment for the human to redirect you; end with an actual question,
  not just a report.
