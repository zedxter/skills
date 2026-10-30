# How We Cut Our AWS Bill by 40% Without Slowing Anything Down

Over the last two quarters, our monthly AWS spend dropped by about 40%. Nobody on the team was assigned to "do cost reduction." The work came from a few engineers noticing waste in their own areas, plus one shared dashboard that made the waste visible. This post covers what we found, what we changed, and what we'd do differently.

## Starting with visibility

Our first attempt failed because we couldn't say where the money went. The console's default views showed totals by service, which told us that EC2 was expensive but not which team, environment, or workload was responsible.

We fixed this in three steps:

1. **Enforced cost-allocation tags.** Every resource needed `team`, `service`, and `env` tags. Untagged resources were flagged in a weekly report, and after a grace period, we blocked untagged resources in non-production accounts with a Service Control Policy.
2. **Activated the tags in Cost Explorer** so we could group spend by them.
3. **Built a daily cost dashboard** with a 7-day moving average per service. Spikes became obvious within a day instead of showing up on the monthly invoice.

Once spend was visible per team, the conversations changed. Teams started asking about their own numbers, which did more than any mandate would have.

## What we found

We sorted the waste into five buckets. Roughly in order of impact:

### 1. Over-provisioned compute

Most of our EC2 and RDS instances were sized for peak load that happened a few hours a week. Using CloudWatch metrics over a 30-day window, we identified instances where CPU stayed under 20% at p95 and memory stayed under 40%. We downsized these in stages, one instance family step at a time, and watched latency and error rates after each change.

We also moved stateless workloads onto Graviton-based instances where the service supported them. This was mostly a build-and-test change; the performance was equal or better at a lower price.

### 2. Idle and forgotten resources

This category was embarrassing but easy:

- Staging environments that ran 24/7 but were only used during business hours. We added scheduled start/stop for non-production clusters, which cut their compute cost by about two-thirds.
- Load balancers attached to services that had been decommissioned.
- Unattached EBS volumes and old snapshots from instances that no longer existed.
- Elastic IPs sitting unassociated, which AWS now charges for.

We set up a monthly job that lists these resources and opens a ticket for each owning team, with a 14-day window before deletion.

### 3. Data transfer and NAT Gateway charges

NAT Gateway processing fees were a surprise. Services in private subnets were sending a lot of traffic to S3 and DynamoDB through the NAT. Adding **gateway VPC endpoints** for both services removed that traffic from the NAT path at no charge for the endpoints themselves.

We also found a service pulling large container images across availability zones on every deploy. Moving the image registry replication closer to the clusters reduced cross-AZ transfer.

### 4. Storage tiers and retention

- **S3 lifecycle policies** moved infrequently accessed data to cheaper storage classes after 30 and 90 days. Several buckets had no lifecycle rules at all, so data from years ago was sitting in the most expensive tier.
- **EBS volumes** moved from gp2 to gp3, which is cheaper per GB and lets you set IOPS independently of size.
- **CloudWatch Logs retention** had been "never expire" by default on most log groups. We set explicit retention per log group, usually 30 days for application logs and 365 days only for audit logs. Log ingestion was the bigger cost, so we also trimmed verbose debug logging that had been left on in production.

### 5. Commitment discounts

Once the usage was right-sized, we bought Savings Plans for the steady-state baseline. We deliberately waited until after the first four buckets, because committing to a bloated baseline locks in waste for one or three years.

## Results

Across the two quarters:

- **Total monthly spend:** down roughly 40% from the starting baseline, adjusted for traffic growth over the same period.
- **Compute:** the largest single reduction, mostly from rightsizing and scheduled non-production shutdowns.
- **Data transfer and NAT:** the most dramatic per-category drop, from endpoint changes alone.
- **p95 latency and error rate:** no measurable regression on any service we changed. We checked each change against its own dashboard before moving to the next.

[Replace these bracketed figures with your actual numbers before publishing: starting monthly spend, ending monthly spend, and the per-category breakdown.]

## What we'd do differently

- **Start with tagging and visibility.** We wasted a few weeks optimizing services that turned out to be small. The dashboard would have pointed us at the big items sooner.
- **Make cost a normal review item.** We now include a rough cost estimate in design docs for new infrastructure. It doesn't need to be precise, but it should exist before the resource does.
- **Don't optimize by hand forever.** The scheduled shutdowns, untagged-resource blocks, and idle-resource reports are all automated now. Without that, the waste would have crept back within a year.
- **Expect the rebound.** Teams that spin up a quick test environment and forget about it will recreate the problem. The monthly report and the 14-day deletion window exist for that reason.

## Takeaways

If you're facing a similar bill, the order that worked for us was: make spend visible by team, delete what nobody uses, fix the data path and storage tiers, right-size compute, and only then commit to discounts. Most of the savings came from unglamorous changes that any team could make in its own area, once it could see the cost.
