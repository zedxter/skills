# How We Cut Our AWS Bill by 40% Without Slowing Down

Last year our AWS spend crossed a line where finance started asking questions we couldn't answer quickly. Over two quarters, a small group of engineers brought the monthly bill down by 40% with no customer-visible regressions and no heroic rewrites. This post covers what we did, in the order that mattered, and what we'd do differently.

*Note: the specifics below are illustrative of the playbook. Swap in your own numbers before publishing.*

## Step 0: Make the bill legible

You can't cut what you can't attribute. Before touching any infrastructure, we spent two weeks on visibility:

- **Mandatory tagging** (`team`, `service`, `env`) enforced through a Service Control Policy and a Terraform module check in CI. Untagged resources were reported weekly, then blocked.
- **Cost and Usage Report** delivered to S3 and queried with Athena, with a simple dashboard showing spend per service and per team.
- **Anomaly alerts** in AWS Cost Anomaly Detection, routed to the owning team's Slack channel.

This alone surfaced about 8% of spend that nobody owned: forgotten staging environments, orphaned load balancers, and unattached EBS volumes. Deleting them was the easiest win of the project.

## Step 1: Right-size compute (~12% of the total bill)

Our EC2 and container fleet was provisioned for peak load that happened twice a year. We pulled 30 days of CPU and memory metrics and found that the median instance sat below 15% CPU.

What we did:

1. Used AWS Compute Optimizer recommendations as a starting point, then sanity-checked them against p99 usage rather than averages.
2. Moved Kubernetes workloads to realistic resource requests. Many pods requested 4x what they used, which forced the cluster autoscaler to hold extra nodes.
3. Adopted Karpenter for node provisioning so the cluster could bin-pack onto fewer, better-fitting instances.
4. Migrated eligible workloads to Graviton (ARM) instances, which gave roughly 20% better price-performance for our Go and Node services. Most builds needed only a multi-arch Docker image.

The lesson: right-sizing is a process, not a project. We now review recommendations monthly.

## Step 2: Commit smartly (~10%)

After right-sizing, not before, we bought commitments. Buying first locks in waste.

- **Compute Savings Plans** covering about 70% of our steady baseline. We deliberately left 30% uncovered to retain flexibility.
- **Spot instances** for stateless workers, CI runners, and batch jobs, with diversified instance types and graceful interruption handling.
- Staggered one-year terms so we weren't exposed to a single renewal cliff.

## Step 3: Tame storage and data transfer (~10%)

Storage costs grow quietly. Data transfer costs grow loudly, but only once you look.

- **S3 lifecycle policies** moved infrequently accessed data to Intelligent-Tiering and expired old logs and incomplete multipart uploads.
- **EBS**: migrated gp2 volumes to gp3 (cheaper, with better baseline performance) and deleted old snapshots.
- **NAT Gateway** was a surprise: a large share of its bill was traffic to S3 and DynamoDB. Adding **VPC Gateway Endpoints**, which are free, removed that traffic from the NAT path entirely.
- **Cross-AZ traffic** dropped after we enabled topology-aware routing in Kubernetes and stopped chatty services from crossing zones unnecessarily.
- Put **CloudFront** in front of static assets and public API responses that were cacheable.

## Step 4: Fix the database and logging (~8%)

- Downsized over-provisioned RDS instances and moved dev/test databases to Aurora Serverless or scheduled shutdowns outside working hours.
- Cut CloudWatch Logs costs by reducing log verbosity, setting retention periods (the default is "never expire"), and shipping high-volume logs to S3 instead.
- Reviewed custom metrics, which are billed per metric. A handful of high-cardinality metrics were responsible for a disproportionate share of cost.

## What didn't work

- **Aggressive Spot usage for stateful services** caused incidents. We rolled it back.
- **Manual cleanup scripts** drifted out of date. Automated policies and guardrails held up better.
- **Optimizing too early** on small line items wasted time. We focused on the top ten line items first, which covered most of the savings.

## Keeping the savings

Cost reductions decay without ownership. We made three changes to make this stick:

1. **Cost is a team metric.** Each team sees its monthly spend and unit costs (cost per request, cost per customer) in their regular review.
2. **Cost estimates in pull requests** via Infracost, so infrastructure changes show their price before merging.
3. **A quarterly review** of commitments, idle resources, and new service pricing.

## Takeaways

- Start with visibility and tagging. Everything else depends on it.
- Delete before you optimize, and right-size before you commit.
- Check data transfer and NAT costs. They're frequently the hidden offender.
- Treat cost as an engineering property, like latency or reliability, with owners and dashboards.

None of these techniques are exotic. The 40% came from doing the unglamorous basics consistently, then building habits so the bill doesn't creep back.

If you've had a similar experience, or found a savings lever we missed, we'd love to hear about it.
