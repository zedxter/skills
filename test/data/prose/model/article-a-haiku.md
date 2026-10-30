# Why We Moved Our Internal Services from REST to gRPC

*[Replace the bracketed details with your team's real numbers, service names, and lessons before publishing.]*

For years, our internal services talked to each other over JSON-over-HTTP. It worked. Engineers knew it, every language had a client, and debugging with `curl` was easy. But as we grew from [N] services to [M], the cracks got harder to ignore. This is the story of why we moved our service-to-service communication to gRPC, what it cost us, and what we'd do differently.

## The problems we were living with

**Contracts drifted.** Our REST APIs were documented in a wiki page, an OpenAPI file that was sometimes out of date, and the code itself. When a field changed from `user_id` to `userId` in one service, the consumer found out in production. We had a standing joke that the spec was "aspirational."

**Payloads were big and parsing was slow.** JSON is readable, but it's verbose and string-heavy. On our hottest path, [service X] spent a measurable share of its CPU time serializing and deserializing responses. Moving that path to binary encoding cut payload sizes by roughly [X]% and dropped p99 latency from [A] ms to [B] ms.

**Every client wrote its own plumbing.** Retries, timeouts, auth headers, and tracing were reimplemented, slightly differently, in each service. Some services retried on 500s; some didn't. Some propagated deadlines; most didn't.

**Streaming was awkward.** We had two use cases where the server needed to push a sequence of updates. With REST we bolted on polling or a custom WebSocket protocol. Neither was pleasant to maintain.

## What gRPC gave us

**A schema that is the source of truth.** We write `.proto` files, and the code is generated from them. A breaking change, such as reusing a field number or changing a type, shows up as a failed build, not a production incident. We run `buf breaking` in CI against the main branch to enforce this.

**Generated clients in every language we use.** Go, Python, and TypeScript clients come from the same definitions. Nobody hand-writes an HTTP client anymore.

**Compact binary encoding over HTTP/2.** Multiplexed connections and protobuf payloads made the hot paths noticeably cheaper. Keep in mind that the gains depend on your workload. For our chatty, small-message traffic, they were large. For services that mostly move big blobs, they were smaller.

**Deadlines and cancellation as first-class concepts.** A deadline set at the edge propagates through the call graph. When a user gives up, the work downstream stops. This alone removed a class of wasted computation we hadn't realized we had.

**Streaming as a built-in.** Server-streaming RPCs replaced our custom push protocol for [feature]. The code is shorter, and the failure modes are ones the framework already handles.

**Interceptors for the cross-cutting concerns.** Auth, tracing, metrics, and retry policy now live in shared middleware. A fix ships once instead of [N] times.

## What it cost us

We don't want to pretend this was free.

- **Debugging is harder at first.** You can't `curl` a gRPC endpoint and read the response. We adopted `grpcurl` and built a small internal tool that renders requests and responses from the proto descriptors. It took a few weeks to get it right.
- **Browser support needs a proxy.** Our frontend calls go through gRPC-Web and an Envoy proxy. That's another moving part, and we had to learn its configuration.
- **Load balancing is different.** Because HTTP/2 connections are long-lived, a naive L4 load balancer can send all traffic to one backend. We moved to client-side load balancing with [your mechanism here].
- **Learning curve.** Engineers who were comfortable with REST needed time with protobuf field numbering, the rules for evolving messages, and the difference between status codes and gRPC status codes.
- **Migration took longer than we estimated.** We ran both protocols side by side for [N] months. Dual-running is expensive, but it let us move one consumer at a time and roll back if something broke.

## How we migrated

1. **We started with one service pair, not the whole fleet.** We picked a pair with a clear owner on both sides and real performance pressure.
2. **We wrote the `.proto` files from the existing REST contracts first.** That exposed several undocumented fields and inconsistent types, which we fixed before any code changed.
3. **We kept the REST endpoints running** behind the same handlers, so consumers could migrate on their own schedule.
4. **We set a deprecation date** for each REST endpoint and tracked remaining callers with metrics. Nothing got turned off until its traffic reached zero.
5. **We wrote a short internal guide** covering naming conventions, error mapping from our REST error model to gRPC status codes, and the rules for evolving protos.

## Would we do it again?

Yes, for internal service-to-service traffic. The contract discipline alone was worth it. The performance gains were real on the paths we measured, and the shared interceptors have paid off every time we've needed to change a cross-cutting behavior.

We would not switch everything. Public-facing APIs that third parties consume still use REST, because the tooling, caching, and familiarity matter more there than wire efficiency. Our rule of thumb now: **gRPC for internal, typed, high-volume calls; REST for public, cacheable, or browser-first APIs.**

If you're considering the move, start small, write the protos before the code, and budget for the debugging tooling. The protocol is the easy part. The ecosystem around it is where the work is.
