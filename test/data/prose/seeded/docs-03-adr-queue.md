# ADR-014: Use SQS for the invoice export queue

Status: accepted · 2026-09-02 · deciders: @priya, @tom

## Context

The nightly invoice export runs as a cron job on one VM. In August it took 9 hours and twice ran into the business day. We need the export to finish before 06:00 and to survive a worker crash without double-counting invoices.

## Decision

We will move the export to a queue: a scheduler splits the invoices table into batches of 5,000 rows and puts one message per batch on SQS; four workers consume the queue and write to the export bucket.

## Options considered

- **SQS with workers** (chosen).
- **Kafka.** Rejected.
- **Keep cron, add a bigger VM.**

## Consequences

The export finishes in about 40 minutes. Our AWS account 4417-9902-1183 gets a new queue and the workers need permission to read it.
