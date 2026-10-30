# Why We Switched from REST to gRPC for Internal Services

REST worked well for us until communication between services became a significant part of the work.

The problem wasn’t that HTTP and JSON had stopped doing their jobs. It was the coordination around them: keeping clients aligned with APIs, deciding which fields were required, handling failures consistently, and figuring out whether a change would break a service we didn’t own.

We wanted those decisions to become more explicit and easier to enforce.

That’s what led us to gRPC.

## We needed a contract we could build against

A REST API can have a rigorous contract. OpenAPI, generated clients, and good validation can take you a long way.

But those practices require consistent investment. Without it, the effective contract can end up scattered across documentation, server code, client code, and conversations.

With gRPC, we define service methods and message types in Protocol Buffers:

```proto
syntax = "proto3";

package inventory.v1;

service InventoryService {
  rpc GetAvailability(GetAvailabilityRequest)
      returns (GetAvailabilityResponse);
}

message GetAvailabilityRequest {
  string product_id = 1;
}

message GetAvailabilityResponse {
  int32 available_units = 1;
}
```

That definition becomes the input for generating client and server code.

The useful change isn’t just autocomplete. It’s having a shared artifact that both sides build against. A client developer can see the available operations and message shapes without reconstructing them from HTTP examples.

This doesn’t eliminate validation. A string field can still contain an invalid product ID, and a well-formed request can still violate a business rule. The schema describes the structure; the application still owns the meaning.

## We wanted less handwritten integration code

Every service boundary needs some plumbing.

With REST, that often means constructing requests, serializing payloads, interpreting responses, and maintaining client wrappers. Libraries help, but teams can still end up solving those problems differently.

Generated gRPC clients give us a more consistent starting point. Calling another service looks like calling a typed method, with a defined request and response.

There’s a trap here: a remote call is still a remote call. It can time out, fail halfway through, or finish after the caller has stopped waiting.

We wanted the convenience of generated clients without forgetting those failure modes.

## Deadlines became part of the conversation

One of the most valuable changes was treating the lifetime of a request as an explicit concern.

gRPC supports deadlines and cancellation, which gives us a consistent mechanism for expressing how long a caller is willing to wait.

It still requires deliberate configuration. gRPC doesn’t choose sensible deadlines for the system on our behalf.

We also had to distinguish failures that deserve different responses:

- Invalid input should be corrected.
- Temporary unavailability might justify a retry.
- An expired deadline means the caller’s time budget is gone.
- A failed business precondition usually needs a different action.

Standard status codes help communicate those distinctions. They don’t replace an error policy.

Retries need the same care. Retrying an operation that creates a payment or reserves inventory can duplicate effects unless the operation is designed to handle it. A transport change doesn’t make an operation idempotent.

## Performance mattered, but it wasn’t the whole argument

Protocol Buffers use a compact binary representation, and gRPC uses HTTP/2, which supports multiplexing calls over a connection.

Those properties can be useful for frequent service-to-service communication. Smaller messages and efficient connection use are attractive when a request crosses several internal boundaries.

But “gRPC is faster” is too broad to be a useful architecture decision.

The result depends on payloads, implementation, connection management, and what the service actually does. A slow database query won’t become fast because the request arrived as protobuf.

For us, performance was a reason to evaluate gRPC. Contract clarity and consistent integration were stronger reasons to adopt it.

## We accepted a harder debugging experience

REST has an excellent everyday debugging tool: a readable HTTP request.

You can inspect JSON, copy a request into a terminal, and understand a surprising amount without specialized tooling.

gRPC gives up some of that simplicity. Binary payloads aren’t convenient to inspect by eye. Tools such as `grpcurl`, server reflection where appropriate, and good observability become more important.

Generated types also don’t explain why a call failed in production. We still need logs, metrics, and traces that connect the caller’s experience to the server’s work.

That operational investment belongs in the migration budget.

## Schema evolution became a discipline

A shared schema makes change visible. It doesn’t make every change safe.

Protocol Buffer field numbers are part of the wire format. Removing a field means reserving its number so it can’t accidentally be reused later.

We also have to think beyond whether a message can be decoded. A change can preserve wire compatibility while changing behavior in a way that breaks callers.

Adding a field is often easier than changing the meaning of an existing one. That principle shapes how we evolve interfaces.

The goal is independent deployment. If every schema update requires all services to deploy together, we’ve created a distributed release dependency.

## We kept REST where it fit

Our decision was specifically about internal service communication.

REST remains useful for public APIs, browser-facing interfaces, and integrations where broad compatibility and easy inspection matter. We don’t need every boundary to use the same protocol.

Internally, we control more of the environment: the clients, the deployment infrastructure, and the tooling. That makes the costs of gRPC easier to justify.

## What the switch actually changed

Moving to gRPC didn’t fix service boundaries, remove network failures, or turn distributed systems into local function calls.

It gave us a clearer way to define interfaces and a more consistent way to consume them.

That was the real reason for the switch. We wanted less ambiguity between teams: what a service accepts, what it returns, how a call fails, and how its interface can evolve.

REST could have supported many of those improvements with stronger conventions and tooling. For our internal services, gRPC made those practices easier to establish as the default.
