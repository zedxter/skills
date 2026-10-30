---
template: onepager
title: Queue worker for the nightly export
subtitle: From nine hours to forty minutes, with safe retries
author: Platform team
date: October 2026
theme: modern
---

## The problem

The nightly export loaded the whole invoices table into memory. It took 9 hours, needed 14 GB of RAM, and failed six times in Q3. A retried run double-counted invoices.

## What we built

- A queue worker that streams rows in batches of 5,000.
- A run id on every batch; the writer skips ids it has seen.
- Back-pressure: at most 20 batches in flight.

## Results

| Metric       | Before  | After  |
|--------------|--------:|-------:|
| Runtime      | 9 h     | 41 min |
| Peak memory  | 14.2 GB | 380 MB |
| Failed runs  | 6       | 0      |

## What we need

A decision on credit notes by 15 November, and one more quarter of the old cron job as a fallback.

## Contact

Platform team · #platform on Slack · platform@example.com
