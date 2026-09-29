---
name: inbox-triage
description: Sorts a pile of messages/emails into what needs a reply now, what can wait, and what's noise.
tags: [routine, email]
---

# Inbox triage

Use when the human says something like "go through my inbox" or when an
`email:*` event fires and the standing instruction is to triage rather than
reply automatically.

## Triage buckets

1. **Needs a reply today** — direct questions, anything with a deadline in the
   next 48h, anything from someone the human clearly prioritizes (check USER.md/
   MEMORY.md for names of people who matter).
2. **Worth a look, not urgent** — newsletters with something genuinely relevant,
   FYI threads, receipts/confirmations that might matter later.
3. **Noise** — promotions, automated notifications, spam-adjacent. Don't act on
   these beyond mentioning the count; never unsubscribe or delete without being
   asked (that's a real-world side effect the human should decide on).

## What to produce

- A short summary grouped by bucket, most urgent first. For "needs a reply
  today" items, include a one-line suggested reply angle (not a full drafted
  email unless asked — drafting and *sending* are different levels of trust).
- If the email/messaging connector supports drafting a reply, offer to draft
  (not send) the top 1–2 urgent ones, and say so explicitly rather than doing
  it silently.
- Put the summary in the human's Inbox with `notify` if this ran unattended
  (heartbeat/automation); reply normally if they asked in chat.

## Rules

- Never send anything on the human's behalf from this skill — sending is a
  separate, explicitly-approved action (the `send_email` tool is `ask` by
  default for a reason).
- If you're not sure whether something is urgent, say so rather than guessing
  confidently — a wrong "not urgent" call is worse than admitting uncertainty.
