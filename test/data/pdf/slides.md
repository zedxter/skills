---
template: slides
title: The nightly export, rebuilt
subtitle: Platform review, Q3 2026
author: Platform team
date: 2026-10-30
---

# Why we changed it

## Nine hours and 14 GB

- The cron job loaded the whole invoices table into memory
- Six failed runs in Q3
- A retried run double-counted invoices

## What we built

1. Stream rows in batches of 5,000
2. Run id on every batch, skip what we have seen
3. At most 20 batches in flight

## Results

| Metric       | Before  | After  |
|--------------|--------:|-------:|
| Runtime      | 9 h     | 41 min |
| Peak memory  | 14.2 GB | 380 MB |
| Failed runs  | 6       | 0      |

# Next

## Decisions we need

- Credit notes in the same export?
- Keep the cron job one more quarter?
