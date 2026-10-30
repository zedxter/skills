Our nightly invoice export took 9 hours in August. It now runs in about 40 minutes, and the speedup wasn't the hard part.

It was a cron job on a single VM. Twice that month it was still running the next morning, so invoices went out late. Customers noticed, and finance was not happy.

We moved it to a queue: SQS with 4 workers.

Most of the speedup didn't come from the workers, though. The old job loaded the whole invoices table into memory. Each worker now streams rows in batches of 5,000.

The hard part was idempotency. When a batch failed and got retried, its invoices were double-counted [TODO: did any double-counted invoices reach customers, or was it caught internally?]. That one bug cost us two weeks. Now every batch carries a run id, and the writer skips any id it has already seen, so a retried batch doesn't get written twice. [TODO: confirm the id is unique per batch and stays the same when that batch is retried]

Priya and Tom did the work. Thank you both.

If you've moved a batch job onto a queue, how did you deal with retries? Did you go with run ids like we did, or something else?