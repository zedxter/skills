In August, our nightly invoice export took 9 hours.

Twice it ran into the morning, so invoices went out late. Customers noticed, and finance was not happy.

The job was a cron task on a single VM. We moved it to a queue: SQS plus 4 workers. It now runs in about 40 minutes.

Most of that improvement had nothing to do with the queue. The old job loaded the entire invoices table into memory. The new worker streams rows in batches of 5,000. Parallel workers helped, but streaming did most of the work.

The hard part was idempotency. Once you have a queue, you have retries, and a retried batch was counting invoices twice. The fix is simple to describe: every batch carries a run ID, and the writer skips IDs it has already seen. Finding the bug took two weeks.

What I'd tell anyone planning a similar move:
→ Before you add parallelism, check how your job uses memory. You might not need parallelism.
→ Design for retries from day one. If your writes aren't idempotent, a queue will double-count.
→ Plan time for the bugs that only show up when things retry.

Priya [TODO: tag] and Tom [TODO: tag] did this work. Thank you both. Invoices go out on time again.

If you've moved a batch job from cron to queue workers, I'd like to hear how it went. What caught you off guard? How did you handle idempotency? Tell me in the comments.

#engineering #softwareengineering #logistics #aws