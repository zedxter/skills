---
title: Moving the nightly export to a queue worker
author: Platform team
date: 2026-10-30
---

The nightly export used to run as a cron job that loaded the whole `invoices` table into memory. It took nine hours on a good night. The queue worker that replaced it streams rows in batches of 5,000 and finishes in about forty minutes.

## What changed

- **Streaming.** The worker reads with a server-side cursor instead of `SELECT *` into a list.
- **Idempotency.** Every batch carries a run id; the writer skips ids it has already seen, so a retried batch no longer double-counts invoices.
- **Back-pressure.** The queue holds at most 20 batches; the reader waits when it is full.

## Numbers

| Metric            | Cron job | Queue worker |
|-------------------|---------:|-------------:|
| Runtime           | 9 h 05 m |       41 m   |
| Peak memory       |  14.2 GB |      380 MB  |
| Failed runs (Q3)  |        6 |          0   |

## Open questions

1. Should the worker also export credit notes?
2. Do we keep the cron job as a fallback for one more quarter?

> "The hard part was never the speed; it was making a retry safe." — from the incident review

Details are in the design doc at https://wiki.example.com/export-worker.
