---
name: deep-research
description: Multi-source research with citations — compares options, digs past the first page of results, and writes a sourced report.
tags: [research, delegate, writing]
---

# Deep research

Use this when the human wants more than a quick answer: a comparison, a market/landscape
scan, "what's the best X", or "find out everything about Y".

## Steps

1. **Break the question into 3–6 independent angles** (e.g. for "compare A vs B vs C":
   one angle per product, or pricing / reviews / alternatives / recent news as angles).
2. **Fan out with `delegate`**: one helper task per angle. Each task instruction should say
   exactly what to find and to return bullet points **with source URLs**, not prose.
3. Helpers should use `web_search` first, then `web_fetch` (or `browser` for JS-heavy pages)
   on the 2–3 most promising results per angle — don't stop at snippets, read the pages.
4. When `delegate` returns, **synthesize**, don't just concatenate: note agreements,
   contradictions, and gaps. Prefer primary sources and recent dates; flag anything
   older than a year if freshness matters.
5. Write the result as markdown with a short summary up top, then sections per angle,
   each claim followed by its source link. Save it to `shared/<topic>-research.md` with
   `write_file` so other agents (or a `make-a-page` follow-up) can use it.
6. If the human will want to revisit this later or skim on their phone, offer to
   `publish_page` a clean version, or ask if they'd like it repeated on a schedule
   (`schedule`) — e.g. weekly competitor tracking.

## Rules

- Never present a single source as consensus. Say "according to X" when sources disagree.
- Always keep the URLs — a report without citations is not "done".
- If sources conflict on a factual claim (price, date, who-owns-whom), say so explicitly
  rather than picking one silently.
