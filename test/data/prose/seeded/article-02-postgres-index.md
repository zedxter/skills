---
title: How One Index Took Our Checkout API From 820 ms to 90 ms
published: true
tags: postgres, performance, sql, database, backend, devops
---

Our checkout API had a p95 latency of 820 ms in September. Most of it was one query: the one that loads a customer's open orders before we price the cart.

This post first appeared on our company blog last week.

## What the query did

The query filtered `orders` by `customer_id` and `status`, then sorted by `created_at`. Postgres used the index on `customer_id` alone and sorted the rows in memory. For our largest customers that meant reading tens of thousands of rows to return twenty.

```sql
-- run against db-prod-03.internal.acme.net
EXPLAIN ANALYZE
SELECT id, total, created_at FROM orders
WHERE customer_id = $1 AND status = 'open'
ORDER BY created_at DESC LIMIT 20;
```

## The fix

We added a composite index on `(customer_id, status, created_at DESC)`. Postgres now walks the index in order and stops after twenty rows. p95 dropped from 820 ms to 90 ms, a 5x improvement.

```sql
CREATE INDEX CONCURRENTLY orders_customer_status_created
  ON orders (customer_id, status, created_at DESC);
```

## Why it matters

In today's fast-paced world of e-commerce, every millisecond counts. Performance isn't just a technical metric, it's a business imperative, and robust indexing strategies empower teams to unlock seamless customer experiences.

This approach works for every Postgres workload, so you should add composite indexes to all your hot queries.
