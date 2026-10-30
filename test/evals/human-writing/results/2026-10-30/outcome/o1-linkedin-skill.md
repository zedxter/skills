Our nightly invoice export took 9 hours in August. It now runs in about 40 minutes.

Twice in August it ran into the morning and invoices went out late. Customers noticed, and finance was not happy.

The old export was a cron job on one VM, and it loaded the whole invoices table into memory. Priya and Tom moved it to SQS with 4 workers that stream rows in batches of 5,000. Most of the win came from the streaming.

Idempotency was the hard part. A retried batch used to double-count invoices [TODO: why a retry double-counted, e.g. a partial write that reran], and that bug cost us two weeks. Now every batch carries a run id, and the writer skips ids it has already seen.

If you've moved a batch job onto a queue, did you dedupe retries with a run id like we did, or something else?