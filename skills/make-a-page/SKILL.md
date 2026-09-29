---
name: make-a-page
description: Turns a request into a small, self-contained HTML mini-app or page the human can open on their phone via publish_page.
tags: [pages, builder]
---

# Make a page

Use whenever the human wants something they'll look at or interact with
repeatedly — a habit tracker, a dashboard, a calculator, a checklist, a small
game — not a one-off answer.

## Steps

1. Decide: **static page** (a report, an itinerary, a dashboard of numbers you
   already gathered) vs. **mini-app** (needs interactivity — a form, buttons,
   state that persists between visits).
2. For a mini-app that needs to remember state across visits, use
   `localStorage` in the page's own JS — there's no server-side storage for
   published pages, so don't invent an API call that doesn't exist.
3. Write **one self-contained HTML file**: inline `<style>` and `<script>`,
   no build step, no external JS framework unless it's loaded from a CDN
   (`cdn.jsdelivr.net` or `cdnjs.cloudflare.com` are allowed by the page's
   CSP; most other origins are blocked).
4. Design for **phone width first** (390px), rounded corners, generous
   spacing, and support both light and dark (`prefers-color-scheme`) — this
   is meant to feel as nice as the rest of OpenDot, not like a raw HTML dump.
5. Call `publish_page(slug, title, html)`. Pick a short, memorable `slug`
   (lowercase, hyphens). Tell the human the page is ready and that they can
   open it from the Pages tab or the link you return.
6. If they'll want it updated later ("show me my habit streak"), remember
   the slug (`remember`) so you can re-publish (same slug overwrites) instead
   of creating duplicates.

## Rules

- Never fetch the human's private OpenDot API from inside the page — it has
  no auth context there. If the page needs live data, either embed it at
  publish time or keep it purely client-side (localStorage).
- Keep pages small; this is for quick useful tools, not a full web app.
