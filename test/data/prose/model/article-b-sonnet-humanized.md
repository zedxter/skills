# What We Got Wrong Moving a Rails Monolith to Kubernetes

We spent fourteen months moving our main application, a nine-year-old Rails monolith with a Postgres database and a pile of background workers, from a handful of hand-tended virtual machines onto Kubernetes. The migration worked. Nobody was paged at 3 a.m. on cutover day, and the old servers are gone. But the useful parts of the experience were the things that went sideways, so that's what I'll write about.

## We migrated the app before we understood its habits

The first mistake was treating the monolith as a box to be lifted and set down somewhere new. We containerized it in a week and felt clever. Then we deployed it to a staging cluster and watched it fall over in ways that had never happened on the VMs.

The app, it turned out, had been quietly relying on things nobody had written down. It assumed a local disk that persisted between deploys, and it wrote generated PDFs there and read them back an hour later. It assumed that cron ran on exactly one machine. It assumed that the hostname was stable, because a licensing library keyed off it. Each of these was a small thing, and each had been invisible for years because the VMs never changed underneath it.

Kubernetes is hostile to that kind of unspoken assumption. Pods move, restart, and multiply. If your application has a personality, the cluster will find it quickly. We would have saved months by spending the first three weeks reading the code and the production boxes for implicit state, instead of writing Helm charts.

## Memory limits are a negotiation with the garbage collector

Our workers kept getting killed with exit code 137, the signature of the kernel's out-of-memory killer. On the VMs we had never seen this, because the machines had plenty of RAM and a leaky worker simply grew until someone restarted it on Friday.

In a container, the limit is a hard wall. Ruby's memory behavior, with its fragmentation and its reluctance to hand pages back to the operating system, meant that workers would creep upward and then die mid-job. We tried raising limits, which only delayed the deaths. What actually helped was switching the allocator to jemalloc, capping the number of threads per process, and recycling workers deliberately after a fixed number of jobs. None of this was exotic, but we only found it by graphing resident memory per pod for two weeks and looking at the shape of the lines.

The broader point is that resource limits expose problems that were always there. We had been paying for the leak in server size without knowing it.

## Health checks can take your service down

We configured a liveness probe that hit an endpoint which checked the database connection. It seemed responsible. Then Postgres had a brief hiccup, every pod failed its probe at roughly the same moment, and Kubernetes helpfully restarted the entire fleet. A thirty-second database slowdown became a six-minute outage, because all of our pods came back at once and stampeded a database that was just recovering.

The fix was to separate the two questions. Liveness should only answer whether the process is stuck, and it should never depend on anything outside the process. Readiness can check dependencies, because failing readiness only removes a pod from the load balancer and doesn't kill it. We also added startup jitter and tuned the boot sequence so that a mass restart wouldn't hit the database with hundreds of simultaneous connections. Connection pooling in front of Postgres, which we had postponed, moved from "someday" to "this week."

## The database stayed where it was, and that was right

There was real pressure, some of it from me, to move Postgres into the cluster too. Running everything in one place has an appeal. We didn't, and I'm glad. A database is the part of your system where being boring is worth the most, and a managed instance outside the cluster gave us backups, failover, and upgrades that someone else was responsible for testing.

The cost was a network hop and some careful work on connection limits, since many small pods each opening their own pool can exhaust a database quickly. It was a modest price. If you do have a good reason to run stateful systems on the cluster, such as a latency requirement or a compliance rule, go in knowing that you are signing up to operate them, and that the cluster's default behaviors were designed for stateless processes.

## Logs and metrics needed to be rebuilt, not ported

On the VMs, when something broke, we logged in and looked at files. That habit was so deeply ingrained that we didn't notice how much of our debugging depended on it. In the cluster, a crashed pod takes its local logs with it, and the pod may not even exist by the time someone starts investigating.

We had to ship logs to a central store before cutover, and we had to make them structured, with a request ID carried through the web tier and into the background jobs it spawned. That last part took longer than expected, because the monolith had a decade of ad hoc log lines in a dozen formats. We ended up cleaning up the noisiest hundred or so and ignoring the rest, which got us most of the benefit for a fraction of the effort.

## Deploys got faster, and that changed the team more than the infrastructure

The thing I did not predict was the effect on how the team worked. Deploys on the old setup took forty minutes and required one person who knew the sequence. After the move, a deploy was a rolling update that took six minutes and could be started by anyone with merge rights. Rollback was a single command.

Within a quarter, people were shipping smaller changes more often, and the number of large risky releases dropped almost to zero. Nobody had asked for that, and we had not designed for it. It was a side effect of making the cost of a deploy low enough that batching stopped being worth it.

## What I would tell someone starting now

Do not split the monolith and migrate it at the same time. We were tempted, and a few colleagues argued for it, but changing the architecture and the platform in a single motion means that every failure has two possible causes. We kept the application as one deployable unit the whole way through, and when something broke we knew the platform was the variable.

Budget time for the dull work. Most of our fourteen months went to secrets management, network policies, resource tuning, and writing runbooks, and very little went to the part people picture when they imagine a Kubernetes migration. The cluster itself was running within two months. Everything after that was learning what our application actually did.

And keep the old environment alive longer than feels necessary. We held onto the VMs for six weeks after cutover, and we used them twice, once to compare behavior against a regression that we couldn't explain and once to confirm that a slow query was our fault and not the cluster's. Having a known-good reference to check against was worth far more than the money it cost.
