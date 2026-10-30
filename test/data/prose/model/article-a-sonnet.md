# Why We Moved Our Internal Services from REST to gRPC

*A practical look at what drove the decision, what it cost us, and what we'd tell other teams.*

A few years ago, our backend was a tidy set of REST services speaking JSON over HTTP. It worked. Then it grew, and the cracks started to show. This post covers why we moved our **internal** service-to-service traffic to gRPC, what improved, and where it hurt.

A note up front: we did not touch our public API. External clients still get REST. This is only about the traffic between our own services.

## The Problem We Actually Had

We didn't switch because gRPC was trendy. We switched because of three recurring pains:

1. **Contract drift.** Our OpenAPI specs were written by hand and updated "when someone remembered." Services regularly disagreed about field names, optionality, and enum values. We found out in production.
2. **Client boilerplate.** Every team wrote its own HTTP client wrapper for every dependency, each with different retry, timeout, and error-handling behavior.
3. **Latency and overhead on hot paths.** A single user request fanned out to 8–12 internal calls. JSON serialization and per-request connection overhead added up.

## What gRPC Gave Us

### 1. The contract is the code

With Protocol Buffers, the `.proto` file is the single source of truth. Both the server stubs and client libraries are generated from it.

```proto
syntax = "proto3";

package orders.v1;

service OrderService {
  rpc GetOrder(GetOrderRequest) returns (Order);
  rpc StreamOrderUpdates(StreamOrderUpdatesRequest) returns (stream OrderUpdate);
}

message GetOrderRequest {
  string order_id = 1;
}

message Order {
  string order_id = 1;
  string customer_id = 2;
  OrderStatus status = 3;
  repeated LineItem items = 4;
}

enum OrderStatus {
  ORDER_STATUS_UNSPECIFIED = 0;
  ORDER_STATUS_PENDING = 1;
  ORDER_STATUS_SHIPPED = 2;
  ORDER_STATUS_DELIVERED = 3;
}
```

If the schema and the implementation disagree, the build fails. Contract drift went from a production problem to a compile-time problem.

### 2. Breaking changes are caught in CI

We added [Buf](https://buf.build) to our pipeline. Its breaking-change detector compares a PR's schema against `main` and blocks changes like removing a field or changing a type. That single check prevented several incidents in the first quarter alone.

### 3. Generated clients, consistent behavior

No more hand-rolled HTTP wrappers. Every team gets a typed client in their language, and we centralized cross-cutting behavior in interceptors: deadlines, retries, auth propagation, tracing, and metrics. A new service gets all of it for free.

### 4. Performance where it mattered

Protobuf is a compact binary format, and gRPC runs over HTTP/2 with multiplexed, long-lived connections. On our hottest fan-out path, we saw:

- Payload sizes drop by roughly 60–70%
- p99 latency improve noticeably, mostly from less serialization work and no repeated connection setup
- Lower CPU usage on the services doing the most serialization

Your numbers will vary with payload shape and language. Benchmark your own workload before assuming a win. For small, infrequent calls, the difference may be negligible.

### 5. Streaming is a first-class feature

Server streaming, client streaming, and bidirectional streaming are built in. We replaced a polling endpoint and a clumsy webhook setup with a single streaming RPC for order updates. The code got simpler and the updates got faster.

## What Hurt

It wasn't all upside. Be honest with yourself about these costs.

**Debugging is harder.** You can't just `curl` an endpoint and read the response. We adopted `grpcurl` and gRPC reflection in non-production environments, and added good logging, but it took the team a while to adjust.

**Load balancing needs thought.** gRPC's long-lived HTTP/2 connections defeat simple L4 load balancers: all requests from one client pile onto one backend. We had to move to client-side load balancing or an L7 proxy (we use Envoy via our service mesh) to distribute load properly. This bit us early.

**Browser support is limited.** Browsers can't speak native gRPC. That's one reason we kept REST at the edge. If you need browser clients, you'll want gRPC-Web or a gateway.

**Tooling and learning curve.** Proto style, versioning conventions, and code generation setup all needed documenting. Some engineers were initially frustrated by the extra step between "edit schema" and "run code."

**Observability needed updating.** Our existing HTTP dashboards and alerts didn't map cleanly onto gRPC status codes. We had to rebuild some of them.

## How We Migrated

We did not do a big-bang rewrite. Our approach:

1. **Start with one high-traffic, well-owned service** as a pilot.
2. **Run both protocols side by side.** The service exposed REST and gRPC at the same time, so consumers could migrate on their own schedule.
3. **Establish conventions early:** package naming (`orders.v1`), a style guide enforced by Buf lint, and a standard error model.
4. **Build shared interceptors** before rolling out widely, so every team started with sane defaults.
5. **Deprecate the old endpoints** only after traffic had fully moved.

The whole migration took several months, running in parallel with normal feature work.

## Should You Do It?

gRPC is a strong fit if:

- You have many internal services calling each other
- You work in multiple languages and want consistent clients
- Contract drift or inter-service latency is a real, measured problem
- You have use cases for streaming

Stay with REST if:

- You have a small number of services and things are working
- Your consumers are mostly browsers or third parties
- Your team values the ease of debugging with plain HTTP and JSON above all
- Your performance is already fine

## Final Thoughts

The biggest win for us wasn't speed. It was **confidence**. When the schema is the contract and the compiler enforces it, teams can change services independently without fear of silently breaking each other. The performance gains were a welcome bonus.

If you're considering the move, start small, invest in tooling and conventions early, and measure before and after. And keep REST where it makes sense. It's not an either/or decision.

*Have you made a similar switch, or decided against one? I'd love to hear what drove your decision in the comments.*
