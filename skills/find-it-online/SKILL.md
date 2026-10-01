---
name: find-it-online
description: Read before looking anything up online (flights, hotels, getting from A to B, restaurants and places, product prices, weather, exchange rates, holidays, news, flight status). Lists the source that works first time for each, with an address you can open straight away.
tags: [browser, research]
---

# Find it online

Most of the time is lost trying sites that block robots, or guessing addresses. Go
straight to the source below, fill in the address, and open it with `browser` (`goto`,
then read what comes back). Encode spaces as `%20` or `+`. Put the currency and
language in the address (`curr=GBP`, `hl=en`) so prices come back the way the human
wants them. Google's cookie page is answered for you.

## With the browser

| Need | Open | What you get |
|---|---|---|
| Flights | `https://www.google.com/travel/flights?q=Flights%20from%20London%20to%20Nice%20on%202026-10-14%20returning%202026-10-17%20for%202%20adults&curr=GBP&hl=en` | fares by airline, times, stops; the cheapest is flagged |
| Hotels | `https://www.google.com/travel/search?q=hotels%20in%20Nice%2014%20Oct%20to%2017%20Oct%202026&curr=GBP&hl=en` | prices across booking sites, ratings |
| Hotels and flats, second opinion | `https://www.booking.com/searchresults.en-gb.html?ss=Nice&checkin=2026-10-14&checkout=2026-10-17&group_adults=2&no_rooms=1` · `https://www.airbnb.co.uk/s/Nice/homes?checkin=2026-10-14&checkout=2026-10-17&adults=2` | |
| Getting from A to B (all ways) | `https://www.rome2rio.com/map/London/Nice` | train, bus, flight, ferry, drive, with rough prices and times |
| A route in a city | `https://www.google.com/maps/dir/?api=1&origin=Kings+Cross+London&destination=Heathrow+Airport&travelmode=transit&hl=en` (`driving`, `walking`, `bicycling`) | options with durations and lines |
| Restaurants, shops, anything nearby | `https://www.google.com/maps/search/ramen+near+Kings+Cross+London?hl=en` | names, ratings, reviews count, open now |
| What something costs | `https://www.google.com/search?udm=28&q=sony+wh-1000xm5&hl=en&gl=uk` (Google Shopping) · `https://www.amazon.co.uk/s?k=sony+wh-1000xm5` | prices across shops |
| Flight status | `https://www.flightaware.com/live/flight/BAW336` (the ICAO code: BA → BAW, U2 → EZY, FR → RYR) or a Google search for `BA336 status` | |
| A quick fact, opening hours, "is X open today" | `https://www.google.com/search?q=...&hl=en` | the answer box at the top |

Change `.co.uk` / `gl=uk` / `GBP` to wherever the human is.

## Without a browser (fast, never blocked)

Use `web_fetch` on these; they answer in plain data and need no key.

- Weather: find the place with `https://geocoding-api.open-meteo.com/v1/search?name=Nice&count=1`,
  then `https://api.open-meteo.com/v1/forecast?latitude=43.70&longitude=7.27&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max&timezone=auto`
- Exchange rates: `https://api.frankfurter.dev/v1/latest?base=GBP&symbols=EUR,USD,CNY`
- Public holidays: `https://date.nager.at/api/v3/PublicHolidays/2026/GB` (any country code)
- News on a topic: `https://news.google.com/rss/search?q=Nice+airport+strike&hl=en-GB&gl=GB&ceid=GB:en`
- What something is: `https://en.wikipedia.org/api/rest_v1/page/summary/Nice`

## When a site says no

- A result marked `blocked` means that site won't let you in from here. Don't retry it
  or its sister sites (Kayak, momondo and others share one wall). Use the next source in
  the table, or tell the human what you found so far and offer to let them take over
  your browser in the Computer panel.
- If a site matters for the task and keeps failing, `find_skills` may have a playbook
  for it; propose the best one to the human (installing always asks them).
- Never make up an address deeper than the ones above. Get it from search results or
  from a page's links.
- Say where each price came from and when you saw it; prices move.
- Don't book or pay. Get everything ready and hand the human the link.
