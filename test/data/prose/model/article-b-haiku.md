# Moving a Monolith to Kubernetes: What We Wish We'd Known

Kubernetes was never the hard part. The hard part was the application we'd been running for nine years, which had grown around assumptions that a container platform quietly breaks. This is a summary of what we learned moving a large Rails-and-Postgres monolith, along with its background workers and a handful of cron jobs, onto Kubernetes. If you're planning a similar move, I hope some of this saves you a few weeks.

## 1. Containerizing is the easy part

Writing a Dockerfile took an afternoon. Making the app behave correctly inside one took much longer, because the monolith had accumulated habits that only worked on long-lived servers:

- **It wrote files to local disk.** User uploads, generated PDFs, and cached exports all landed in `/var/app/tmp`. Pods are ephemeral, so those files vanished on every deploy and every reschedule. We moved them to object storage before we moved anything else.
- **It assumed one process per host.** Some initializers started background threads that ran in every process, which meant duplicate jobs once we scaled replicas. Anything that must run exactly once belongs in a dedicated workload, not in app boot.
- **It read config from files that were baked in at build time.** That made one image impossible to promote across environments. We switched to environment variables injected from Secrets and ConfigMaps, and we added a startup check that fails fast when a required variable is missing.

**Lesson:** Before touching the cluster, audit the app for anything that depends on a specific machine staying put. Search for local file writes, in-process schedulers, and config read at build time.

## 2. Health checks will lie to you if you let them

Our first readiness probe hit the homepage. It passed even when the database connection pool was exhausted, so Kubernetes kept routing traffic to pods that could only return 500s. We then made the probe too strict. It checked the database, and a brief Postgres failover marked every pod unready at once. The rolling deploy stalled and the whole service dropped out.

What worked:

- **Liveness** checks only that the process can respond. It should not touch dependencies. A failing liveness probe restarts the container, so a database blip should never trigger one.
- **Readiness** checks that the pod can serve requests, with a short timeout and a tolerance for transient failures (`failureThreshold` above 1).
- **Startup probes** give slow-booting Rails apps time to load without the liveness probe killing them during boot.

**Lesson:** Each probe answers a different question. Give each one its own job, and test what happens when a dependency goes away.

## 3. Resource limits are a guessing game until you measure

We set CPU and memory requests by copying numbers from a staging environment. In production the Ruby processes grew steadily, and pods were OOMKilled every few hours. Setting limits too low caused restarts. Setting them too high wasted a lot of cluster capacity, and the scheduler packed nodes badly.

Our approach:

1. Run the app under realistic load for a week and record actual usage per process type.
2. Set memory requests near the observed steady state and limits with real headroom.
3. Tune the Puma worker and thread counts to match the container's CPU allocation. Running eight workers in a container limited to one CPU just produces throttling.
4. Watch CPU throttling metrics, not only CPU usage. A pod can look idle and still be starved.

**Lesson:** Requests and limits are a capacity-planning exercise, not a configuration detail. Budget for them the way you would budget for a database.

## 4. Split the workloads, but not too early

We split the one app into several Deployments: web, API, Sidekiq workers by queue, and a scheduler. This was clearly right, because each one scales on different signals. But we did it before we understood the traffic, and we created six deployments where two would have served us better for months.

We recommend starting with the smallest split that gives you independent scaling and independent restarts: web and workers. Add more workloads when a specific queue or endpoint has a real reason to scale separately.

Cron jobs deserve a separate note. Kubernetes `CronJob` objects are convenient, but they don't prevent overlapping runs by default, and a job that takes longer than its interval will pile up. Set `concurrencyPolicy: Forbid`, set a `startingDeadlineSeconds`, and make every job idempotent. Retries will happen.

## 5. Database connections multiply faster than you expect

This one hurt us in production. Every pod opened its own connection pool. We went from a handful of processes to dozens of pods, each with a pool of 10 to 20 connections, and Postgres ran out of connection slots during a traffic spike. The failure looked like a database outage even though the database was fine.

Fixes that helped:

- Compute the total connection budget before scaling anything: `pods × processes × pool size`, plus workers and admin tools.
- Put PgBouncer in transaction-pooling mode between the app and Postgres. Check that your ORM and any session-level features (advisory locks, `SET` statements, prepared statements) are compatible with it.
- Cap autoscaling so that a traffic spike can't exceed the connection budget.

**Lesson:** The database is a shared resource that Kubernetes will happily overwhelm. Treat connection limits as part of the deployment design.

## 6. Autoscaling needs a signal that matches your bottleneck

The Horizontal Pod Autoscaler scaled on CPU at first. Our web tier was usually waiting on the database, not burning CPU, so the HPA barely reacted until latency had already degraded. The workers were the reverse: they were CPU-light but queue-heavy, so CPU told us nothing.

We moved workers to scale on queue depth, using a custom metric exported from Redis, and web to scale on request latency and concurrency. Scaling up is fast; scaling down needs a stabilization window, or the cluster will thrash.

**Lesson:** Pick the metric that reflects the thing that is actually saturated. CPU is the default because it is easy to measure, not because it is usually the right signal.

## 7. Deployments are where people notice

For the first few months, deploys were the most visible failure mode. A few things we learned:

- **Run migrations as their own Job**, not as part of app startup. If every replica runs migrations at boot, you get races, and a long migration blocks the rollout. Make migrations backward-compatible, so the old and new versions can run side by side during a rolling update. That means adding columns before code uses them, and removing them only after nothing reads them.
- **Set `maxUnavailable` and `maxSurge` deliberately.** The defaults are fine until your pods take 90 seconds to become ready, and then a rollout takes an hour.
- **Respect `terminationGracePeriodSeconds`.** Puma and Sidekiq need to finish in-flight work on SIGTERM. Verify that your app actually handles the signal, and that its drain time fits inside the grace period. Otherwise every deploy drops requests and kills jobs mid-run.
- **Use a `preStop` hook or a short sleep** so that load balancers stop sending traffic before the process begins shutting down.

## 8. Observability has to come before the cutover

We cut over before we had good dashboards for the new environment. For two weeks, debugging meant running `kubectl logs` across pods and hoping. Get these in place first:

- Structured logs shipped off-cluster, with the pod, namespace, and request ID on every line.
- Metrics for request latency, error rate, queue depth, pool usage, and restart count.
- Alerts on symptoms users would feel, such as error rate and latency, not on every pod restart.

Also watch for events. Pod evictions, failed image pulls, and scheduling failures show up as Kubernetes events and disappear after an hour unless you capture them.

## 9. Keep the old system alive until you trust the new one

We ran the old and new environments side by side and shifted traffic in stages: 1%, then 10%, then 50%, then 100%. Each stage had a defined rollback plan. Rollback only works if the old system still receives data, so during the transition we kept writes flowing to both paths where feasible, and we tested the rollback procedure itself before we needed it.

## What we'd do differently

If we started again, we would:

1. **Audit the app for machine-local assumptions** before writing any manifests.
2. **Build the observability stack first**, then migrate.
3. **Measure resource usage under load** instead of copying numbers.
4. **Account for database connections** as a fixed budget from day one.
5. **Start with fewer workloads** and split only when a real scaling need appears.
6. **Practice rollbacks** on a schedule, not just in an incident.

Kubernetes gave us repeatable deploys, self-healing, and a clearer picture of how the system actually runs. It also exposed every shortcut we had taken in the monolith. The migration was worth it, but the most valuable outcome was not the platform. It was the discipline of learning what our application actually needed to run reliably.
