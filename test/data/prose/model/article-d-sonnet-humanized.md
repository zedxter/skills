# How we cut our AWS bill by 40%

Our AWS bill reached $212,000 a month in March, up from about $140,000 the previous summer. Traffic had grown by roughly a quarter over that time, so most of the increase had no good explanation. Nobody had decided to spend that money. It had piled up.

Two engineers worked on it part time for five months, with some help from finance. The bill is now $127,000 a month, and traffic is higher than it was in March. The savings below are listed in the order we found them, which is close to the order of their size.

## Start by finding out who owns what

The first view in Cost Explorer showed EC2 at 41% of spend, RDS at 18%, data transfer at 12%, S3 at 11%, and a long tail. That told us where the money went but not why. About 30% of our spend had no team tag, so we couldn't ask anyone about it.

The first two weeks went to tagging. We wrote a script that walked every resource, guessed an owner from the CloudFormation stack or the IAM role that created it, and opened a pull request against the owning team's repo. It was dull work, but every later step depended on it. Each saving below started with someone saying "oh, that one is mine."

## Compute: $38,000 a month

The median CPU utilization across our EC2 fleet was 9%. Instance types had been chosen at launch and never revisited. We pulled 30 days of CloudWatch data, flagged anything with a p95 under 40%, and sent each team a list with a suggested smaller size. Most accepted without argument. A few services had memory-bound workloads that looked idle on CPU, and we left those alone.

We moved about two thirds of the fleet from m5 to m7g (Graviton). For most services this meant changing the base image and rebuilding. Two services depended on an x86-only native library, and they stayed where they were.

Only after rightsizing did we buy a Savings Plan. We bought a one-year, no-upfront compute plan covering about 70% of the steady baseline. Buying earlier would have meant committing to capacity we were about to stop using, and the discount would have been applied to waste.

The nightly batch workers went to Spot. They checkpoint every few minutes, so a two-minute interruption notice costs them very little. We tried Spot for the API tier as well and reverted within a week, because p99 latency spiked every time a node was reclaimed.

## Data transfer: $16,000 a month

NAT gateways charge $0.045 per GB processed, and this was the most surprising item in the bill. One service was pulling container images from ECR and reading S3 objects through a NAT gateway, adding a charge on every byte. An S3 gateway endpoint is free, and we added interface endpoints for ECR. That change alone saved about $9,000.

The other large item was cross-AZ traffic from Kafka. Consumers were reading from partition leaders in other availability zones, and cross-AZ transfer is billed in both directions. Enabling follower fetching with rack awareness let consumers read from a replica in their own zone. It took a broker config change and a rolling restart.

## RDS: $14,000 a month

Several Postgres instances were still sized for a traffic peak from 2021 and used io1 storage with provisioned IOPS that nothing came close to using. We moved them to gp3, which let us set IOPS and throughput independently, and resized the instances to match observed load.

We also dropped a read replica. Connection logs showed that nothing had connected to it in four months. Separately, staging and development databases ran around the clock, and we scheduled them to stop overnight and on weekends.

## S3: $9,000 a month

We had about 400 TB of application logs in S3 Standard with no expiry rule. Most had never been read after the first week. A lifecycle rule now moves logs to Infrequent Access after 30 days and deletes them after 400. One bucket had versioning on and kept every noncurrent version forever, and a rule there cleared out several hundred terabytes of old object versions. We also found 30 TB of incomplete multipart uploads, which cost the same as completed ones and are easy to clean up with a single lifecycle rule.

## What didn't work

We moved one batch workload to a cheaper region. The compute savings were real, but the data had to be shipped there and back, and transfer charges wiped them out. We also spent a week evaluating a third-party cost optimization product. It recommended mostly what Cost Explorer's rightsizing view already showed us, so we dropped it.

## Keeping it down

The remaining $8,000 came from small items: unattached EBS volumes, old snapshots, idle load balancers, and CloudWatch log groups with infinite retention. None was large, and together they were worth cleaning up.

To stop the bill from drifting back, each team now gets a weekly cost report in Slack, broken down by service. An alert fires when any team's spend runs 15% above its trailing four-week average. Resources without an owner tag are rejected at deploy time by a service control policy.

The Savings Plan runs out next March. By then our instance mix will have changed, so we plan to redo the utilization analysis before renewing, not simply buy the same plan again.
