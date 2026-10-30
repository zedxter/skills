In August, our nightly invoice export took 9 hours.

Twice it ran into the morning, and invoices went out late. Customers noticed, and finance was not happy.

The export was a cron job on a single VM. We moved it to a queue worker: SQS plus 4 workers. It now runs in about 40 minutes.

Most of the speedup didn't come from the queue. It came from how we read the data. The old job loaded the whole invoices table into memory. The new worker streams rows in batches of 5,000.

Speed wasn't the hard part. Idempotency was.

Once you have a queue, you have retries. When a batch was retried, its invoices got counted twice. That bug cost us two weeks.

The fix: every batch now carries a run id, and the writer skips ids it has already seen. It's simple once you see it, but finding it was painful.

What I'd tell anyone planning a similar move:
→ Check how you read your data before you add more machines.
→ Plan for retries from day one. A queue will retry, so decide early what happens when the same work runs twice.

Huge thanks to Priya [TODO: tag] and Tom [TODO: tag], who did the work.

If you've moved a batch job from cron to queue workers, I'd like to hear how it went. What bit you? How did you handle idempotency? Please share in the comments 👇

#engineering #softwareengineering #aws #distributedsystems