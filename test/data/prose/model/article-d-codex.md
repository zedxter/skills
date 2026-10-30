# How We Reduced Our AWS Bill by 40%

We reduced our AWS bill by 40% by making a series of changes to how we provisioned, operated, and paid for infrastructure.

The work started with a simple question: which parts of our bill were actually necessary to serve our workload?

That question turned out to be harder—and more useful—than asking which AWS services were expensive.

## First, make the bill explainable

A monthly total tells you how much you spent. It gives you very little guidance about what to change.

We broke spending down by service, environment, and workload, then assigned owners to the largest recurring costs. Shared infrastructure needed extra attention: costs without a clear owner were easy for every team to overlook.

We also separated recurring usage from temporary spikes. A migration, load test, or backfill can make a service look permanently expensive when the underlying issue is temporary.

The result was a prioritized list of costs we could investigate, with enough context to distinguish useful capacity from waste.

## Remove resources that no longer have a job

Our first pass focused on resources whose purpose had expired.

Development environments kept running outside working hours. Storage survived the workloads that created it. Temporary infrastructure became permanent because nobody had been responsible for removing it.

These were straightforward savings, but deleting resources still required care. Low utilization alone was insufficient evidence: a quiet resource might support disaster recovery or an infrequent business process.

For each candidate, we checked ownership, dependencies, and retention requirements. We then removed what was unnecessary and added expiration or scheduling rules where appropriate.

The cleanup mattered. Preventing the same waste from returning mattered more.

## Right-size against workload behavior

Next, we reviewed capacity.

We had provisioned some workloads for peaks that rarely occurred and retained those settings as usage changed. Other workloads had inherited instance sizes from earlier deployment decisions.

We examined CPU, memory, throughput, latency, and queue depth across representative traffic periods. Average CPU utilization was useful for finding candidates, but it was never enough to justify a change on its own.

We reduced capacity incrementally and watched application behavior after each adjustment. For services with variable demand, we reviewed scaling thresholds and minimum capacity together.

The constraint was explicit: savings had to preserve our reliability and latency targets. A smaller instance that causes timeouts is an expensive optimization.

## Give stored data a lifecycle

Storage costs accumulate quietly because individual writes are cheap and retention decisions are easy to postpone.

We reviewed what we stored, how frequently we accessed it, and how long it remained useful. That led to clearer retention policies for logs, snapshots, and intermediate data, along with lifecycle rules for eligible objects.

We evaluated the full cost before moving data between storage classes. Retrieval charges, minimum storage durations, and access patterns can erase an apparent saving.

The useful change was making retention intentional. Every dataset needed a reason to exist for its chosen lifetime.

## Buy commitments after reducing waste

Once usage was lower and more predictable, we reviewed commitment discounts.

The order mattered. Buying a discount against an oversized baseline would have locked in part of the inefficiency we were trying to remove.

We covered a conservative portion of steady usage and retained flexibility for uncertain demand. We assessed savings against the resulting bill, accounting for commitment costs, rather than treating a quoted discount percentage as the outcome.

## Make the reduction durable

The combined changes reduced our AWS bill by 40%.

To assess that result, we compared equivalent billing periods and checked for traffic changes, credits, and one-time charges that could distort the comparison. We also tracked cost per unit of workload so that future growth would not obscure whether efficiency was improving.

Then we made cost review part of normal operations: visible ownership, budget alerts, cleanup policies, and regular reviews of the largest spending changes.

The biggest lesson was that infrastructure spending reflects accumulated engineering decisions. Each decision may have been reasonable when it was made. Over time, workloads change while their infrastructure stays the same.

Our savings came from closing that gap—and building a routine for keeping it closed.
