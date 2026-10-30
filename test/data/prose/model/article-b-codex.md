# What Migrating a Monolith to Kubernetes Actually Taught Us

Moving a monolith to Kubernetes looks straightforward on a whiteboard: package the application in a container, write a deployment manifest, add a service, and route traffic to it.

The difficult work starts when the application runs.

A migration exposes assumptions that have accumulated over years: that a process will live for weeks, that files written to disk will still be there tomorrow, that deployments happen one machine at a time, and that the database can handle every connection the application opens.

The most useful lessons come from finding those assumptions and deciding which ones to change.

## Keep the first move small

It is tempting to combine a Kubernetes migration with a microservices rewrite. Both promise cleaner boundaries and more independent deployments. Doing them together also makes failures harder to understand.

When a request fails, is the cause a new service boundary, a networking rule, a changed configuration value, or an application bug?

A narrower first migration gives you a baseline. Keep the monolith intact, preserve its behavior, and change how it is packaged and operated. Extract services later when there is a clear reason, such as a workload that needs different scaling or a component with a distinct release cycle.

A monolith can be a perfectly reasonable Kubernetes workload.

## Make hidden state visible

Local disk is often the first surprise.

An application may store uploads, generated reports, or temporary exports beside its code. That works until a replacement pod starts on another node or a second replica receives the next request.

Before adding replicas, inventory everything the application writes and everything it expects to remember:

- Put durable uploads and artifacts in shared storage suited to their access patterns.
- Move sessions out of process memory, or deliberately account for their loss.
- Give temporary files explicit lifetimes and cleanup behavior.
- Identify scheduled jobs that must not run independently in every replica.

The important question is: what happens to this data when the process disappears?

## Treat shutdown as part of the request lifecycle

Startup gets plenty of attention during a migration. Shutdown deserves just as much.

During a rollout, old and new application instances coexist. A terminating instance may still be handling requests, maintaining connections, or processing background work.

The application needs a deliberate response to termination: stop accepting new work, finish or safely abandon existing work, close resources, and exit within the configured grace period.

Background jobs need particular care. If interrupted work is retried, can it safely run twice? A deployment should never turn a retry into a duplicate payment, email, or import.

Test termination while the application is busy. An idle pod shutting down cleanly proves very little.

## Give health checks distinct jobs

A process can be alive while it is unable to serve traffic. It can also be temporarily unable to serve traffic without needing a restart.

That distinction matters:

- A startup probe allows initialization to finish before other probes take over.
- A readiness probe determines whether an instance should receive traffic.
- A liveness probe detects conditions that require restarting the process.

One costly mistake is making liveness depend on every downstream service. A database outage can then trigger repeated application restarts, adding connection churn precisely when the database is struggling.

Health checks should drive useful recovery behavior. Design them around the action Kubernetes will take when they fail.

## Scaling the application can overwhelm the database

Increasing replicas is easy. Increasing downstream capacity takes more thought.

Each new replica may create its own connection pool, cache, worker threads, and polling loops. Ten replicas with a maximum pool size of fifty connections can attempt to consume five hundred database connections.

Rolling deployments can temporarily increase that number further as old and new replicas overlap.

Build a connection budget before enabling aggressive scaling. Include web processes, background workers, deployment overlap, and operational headroom.

Then measure whether additional replicas improve throughput. If the bottleneck is a slow query or a saturated database, more application instances may only create a larger queue.

## Database migrations need their own deployment strategy

An application rollout can be reversed quickly. A database change may be much harder to undo.

This becomes especially important when two application versions run at once. Both must tolerate the schema during the transition.

For significant changes, an expand-and-contract approach helps:

1. Add the new schema alongside the old structure.
2. Deploy code that can work through the transition.
3. Backfill existing data and verify it.
4. Switch reads and writes as appropriate.
5. Remove the old structure after older application versions are retired.

Run migrations through an explicit, controlled mechanism. Having every replica attempt them during startup creates avoidable contention and uncertainty.

A rollback plan is only credible if the previous application version still works with the current database.

## Build observability before you need it

On a familiar server, troubleshooting often begins with logging in and looking around. In Kubernetes, the process that failed may already have been replaced.

Useful diagnostics must survive it.

Centralize logs, attach request identifiers, and make it possible to connect application errors with deployment events. Watch user-facing signals such as error rate and latency alongside resource usage.

Know how to answer a few practical questions:

- Which application version handled the failing request?
- Was the container restarted or killed for exceeding memory limits?
- Did latency increase before or after the rollout?
- Is the delay inside the application or in a dependency?

A dashboard full of cluster metrics cannot replace visibility into whether users can complete their tasks.

## Rehearse failure before cutover

A successful deployment is a weak test of operational readiness.

Kill a pod during traffic. Drain a node. Interrupt a background job. Deploy an image that fails readiness. Roll back while requests are in flight.

These exercises reveal gaps that a normal staging walkthrough misses: dropped requests, duplicate jobs, missing alerts, or a rollback procedure that depends on knowledge held by one person.

They also make runbooks concrete. “Restore the previous version” becomes a tested sequence with known limitations.

## Account for the platform’s operating cost

Kubernetes introduces a substantial set of responsibilities: cluster upgrades, access controls, networking, resource management, certificates, storage, and incident response.

Someone needs to own them.

Standard deployment templates, managed infrastructure, and a small set of supported patterns can reduce the burden. Every custom controller or clever deployment mechanism adds something the team must understand during an outage.

The migration is successful when the people operating the application can explain how it behaves and recover it reliably.

## Measure the result in everyday work

The useful outcomes are practical: deployments become more predictable, recovery becomes faster, capacity decisions become clearer, and fewer releases require manual intervention.

Those improvements come from making application behavior explicit—where state lives, how work stops, how versions coexist, and how failure is detected.

Kubernetes gives those decisions a place to run. The migration succeeds when the team has made them deliberately.
