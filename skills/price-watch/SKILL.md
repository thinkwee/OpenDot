---
name: price-watch
description: Watches a product's price over time and tells the human when it drops — sets up the recurring check itself.
tags: [routine, browser]
---

# Price watch

Triggered by "watch the price of X" / "tell me if Y drops below Z".

## First run

(Don't have the product page yet? `read_skill('find-it-online')` says where to look.)

1. Get the product page open — try `web_fetch` first; if the price isn't in
   the static HTML (common on JS-heavy shops), use `browser` (`goto`, then
   `read`) instead.
2. Extract the current price and confirm you found the *right* price (not a
   related/similar item, not a "was" price) — say what you found before
   setting anything up.
3. `remember` the product URL, target price (if the human gave one) and the
   price you saw today, e.g. "Price watch: <url> — target £X, was £Y on
   <date>." so future runs have the baseline without re-deriving it.
4. `schedule` a `cron` automation (once or twice a day is usually enough —
   e.g. `0 9,18 * * *`) with a clear `prompt` that repeats the URL and target
   price so the automation is self-contained.

## Each scheduled run

1. Re-fetch the page the same way and extract the current price.
2. Compare to the target (or to the last known price if no target was set —
   "notify me of any drop").
3. If it dropped to/below target, or dropped meaningfully (>5%) with no
   target set, `notify` the human immediately with the price, the drop, and
   the link. Otherwise, stay quiet — don't notify on every check, only on
   news.
4. Update the remembered "was £Y on <date>" line so the next comparison is
   against the latest known price, not the original.

## Rules

- Never attempt to buy/checkout — that's a separate, explicitly-approved
  action. This skill only watches and reports.
- If the page structure changes and you can't find a price confidently, say
  so in the notification rather than reporting a wrong number.
