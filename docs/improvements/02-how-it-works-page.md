# 02 · A plain "How it works" page

Status: **done and live (2026-10-04)**

## Situation

Someone opening StreamPulse saw charts and a chatbot but nothing that said where the numbers come from, how they are
prepared, or why they can be trusted. The explanation lived only in the README. The site is meant to show *how the
system works*, not to sell it, so a visitor should be able to find that out without leaving the site.

## Task

One page, linked from the navigation and the home page, that answers three plain questions (where the data comes from, how
it is processed, how it is shown), uses the live numbers instead of typed ones, and stays short enough to read in a minute.

## Action

- `dashboard/app.js`: new `pages.how`, route `#/how`. It reads `meta.json` and `health.json` (already published every day), so the row
  count, market count, date range, last refresh time, check count and the list of markets with source gaps are never hardcoded.
- Three cards: (1) the Kaggle source and what "streams" means, with the source gaps named; (2) the five processing steps (download,
  Bronze, Silver, Gold, site files) in one line each, plus last refresh and a link to the Health page; (3) how the pages and the chatbot
  show the data, in two short paragraphs.
- `dashboard/index.html`: "How it works" in the navigation; `app.js` home page index gets a matching row; `style.css` about 12 lines.
- Alternative rejected: a diagram. A list is easier to keep correct as the pipeline changes, and it reads on a phone.

## Result

- Live at `https://streampulse-site-922120357133.s3.ap-south-1.amazonaws.com/index.html#/how` (checked in headless Chrome after the sync:
  3 cards, nav link present, no page errors).
- Numbers shown on 2026-10-04: 43,925,467 rows, 72 markets, 2017-01-01 to 2026-10-02, 6 of 6 checks passing, gaps listed for Belarus,
  Israel and India, all read from the published JSON.
- Mobile width 390 px: no horizontal overflow.
- Not covered: the text of the five steps is written by hand, so it must be edited if the pipeline changes (the numbers update themselves).

**Rollback:** `git revert` the commit and `aws s3 sync dashboard s3://streampulse-site-922120357133 --exclude "data/*"`.
