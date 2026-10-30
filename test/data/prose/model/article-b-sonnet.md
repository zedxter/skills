# Lessons Learned from Migrating a Monolith to Kubernetes

Eighteen months ago, we took a ten-year-old Rails monolith, with 400k lines of code and a database that had seen things, and started moving it onto Kubernetes. We're done now, mostly. Here is what we wish we had known at the start.

## 1. Don't conflate "move to Kubernetes" with "break up the monolith"

Our biggest early mistake was treating these as one project. They aren't. Kubernetes runs monoliths perfectly well. We spent the first three months debating service boundaries when we should have been figuring out how to get one container to start reliably.

We eventually split the work in two:

1. **Containerize and deploy the monolith as-is.** Same code, same database, new runtime.
2. **Extract services later, only where it pays off.**

The first step gave us rolling deploys, autoscaling, and reproducible environments. All of that came without a single architectural rewrite. We've extracted three services since then, each for a concrete reason (a different scaling profile, a different team owner, a different release cadence). The monolith is still the core of the system, and that's fine.

## 2. Your app has hidden state, and you'll find it in production

On a VM, a monolith can quietly depend on the machine it runs on. We found:

- **Local disk writes.** Uploads, temp files, and generated PDFs were all written to `/var/app/tmp` and read back by a later request. That breaks the moment a second pod exists.
- **In-process caches and sessions.** Users got logged out whenever a pod restarted or the load balancer sent them elsewhere.
- **Cron jobs on "the" server.** We had a crontab on one host that nobody had documented. Duplicating it across pods would have sent every customer the same email several times.
- **Hardcoded hostnames and IPs.** More than we'd like to admit.

Before cutting over, run at least two replicas in staging and send real traffic through them. Statelessness only gets tested when there's more than one instance.

## 3. Graceful shutdown is not optional

Pods get killed all the time: deploys, node drains, autoscaler decisions. Our first rollouts caused bursts of 502s because the app received `SIGTERM` and exited immediately, dropping in-flight requests.

The fix took three parts:

- Handle `SIGTERM` by finishing current requests and then exiting.
- Add a short `preStop` sleep so the load balancer stops sending traffic before the app stops accepting it.
- Set `terminationGracePeriodSeconds` longer than your slowest legitimate request.

Background workers needed the same treatment. A job that gets killed halfway through should be safe to retry. If it isn't, you have an idempotency problem that Kubernetes just made visible.

## 4. Health checks can hurt you

Readiness and liveness probes sound simple, but we got them wrong in instructive ways.

Our first liveness probe hit an endpoint that checked the database. When the database had a brief hiccup, every pod failed its liveness check at once and Kubernetes restarted all of them. A small blip became a full outage.

What we do now:

- **Liveness** answers one question: is this process wedged? It never checks dependencies.
- **Readiness** answers: can this pod serve traffic right now? It can check dependencies, with care.
- **Startup probes** cover our slow boot (about 90 seconds of Rails eager loading), so liveness doesn't kill pods before they finish starting.

## 5. Resource requests and limits need real data

We guessed at first. Pods with memory limits too low were OOM-killed under load. Pods with CPU limits too tight were throttled, and latency crept up with no obvious cause. Meanwhile, requests set too high left half our cluster idle.

Our approach now:

- Measure real usage in staging under realistic load, then set **requests** near typical usage.
- Set **memory limits** with headroom, because memory is not compressible and overruns mean a kill.
- Be cautious with **CPU limits**. For latency-sensitive services, we dropped them and rely on requests for scheduling.
- Revisit the numbers quarterly. Workloads drift.

A Ruby app with a large heap behaves very differently from a Go service, so don't copy numbers from a blog post. That includes this one.

## 6. The database is the hard part

Stateless app pods are the easy part of the migration. The database is where the risk lives. We chose to keep Postgres on a managed service outside the cluster rather than run it inside Kubernetes. That decision removed an entire class of problems and let us focus on the app.

Connection management bit us anyway. Fifty pods each holding a pool of twenty connections is a thousand connections, and Postgres doesn't like that. We added PgBouncer and made pool sizes part of our autoscaling math. Before you scale out your app tier, check whether your database can handle it.

## 7. Invest in observability before you need it

On a single VM, you can SSH in and run `tail -f`. In a cluster, the pod you want may already be gone. Before migrating any real traffic, we put in place:

- Centralized, structured logging with a request ID that follows each request across components.
- Metrics for the "golden signals": latency, traffic, errors, saturation.
- Distributed tracing, once we had more than one service.
- Alerts tied to user-facing symptoms, not just pod restarts.

Having a baseline from the old environment also let us prove the new one performed as well. Without that comparison, every slowdown turns into an argument about whether Kubernetes is the cause.

## 8. Migrate gradually, with an easy way back

We didn't do a big-bang cutover. We ran both environments in parallel and shifted traffic by weighted DNS: 1%, 5%, 25%, 50%, 100%, with days of soak time between steps. Twice we rolled back within minutes after spotting issues we'd missed. Rollback needs to be a boring, rehearsed action rather than a heroic one.

Keep the old environment around for a while after cutover. We kept ours for six weeks. It cost money, and it was worth it.

## 9. Kubernetes has a real cost in complexity

This is the lesson people least like to hear. Kubernetes gave us real benefits, but it also gave us a new set of things to learn and operate: networking, RBAC, ingress, secrets management, cluster upgrades, and YAML sprawl.

A few things reduced the pain:

- **Use managed Kubernetes.** Running the control plane yourself is rarely a good use of a small team's time.
- **Standardize with templates.** We use Helm charts with sane defaults, so teams don't write raw manifests from scratch.
- **Adopt GitOps.** Every change goes through a pull request and is applied by a controller, so the cluster state matches what's in git.
- **Budget for training.** Our team's productivity dipped for about two months before it recovered.

Ask honestly whether you need Kubernetes at all. If your deploys are already smooth and your scaling needs are modest, a simpler platform might serve you better. We had real reasons to migrate: multiple teams, uneven load, and painful deploys. If you don't have similar ones, think twice.

## What we'd do differently

If we started over, we would:

1. Run the "two replicas, real traffic" test on day one.
2. Write the graceful shutdown and probe logic before touching anything else.
3. Keep the monolith intact and resist the urge to decompose it early.
4. Build observability first, before the first user ever hit the new cluster.
5. Budget twice the time we originally estimated.

## Closing thoughts

The migration made our deploys faster and safer and our infrastructure more consistent. It didn't make our code better, and it didn't fix our architecture. Those remain separate problems that need their own attention.

Treat the move to Kubernetes as an infrastructure project with a clear scope, and keep it separate from your architectural ambitions. Do that and you'll avoid most of the pain we went through.
