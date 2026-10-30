# Why we moved our internal services from REST to gRPC

We have about forty services, mostly Go with a few Python ones, all talking to each other over JSON on HTTP. For years that worked fine. This post is about the specific things that made us stop being fine with it, and about what the move cost.

## The bug that started it

A teammate renamed a response field from `customer_id` to `account_id` in the billing service. The OpenAPI file was updated in the same pull request. The tests passed. Two days later someone noticed that the invoicing service had been generating documents with an empty customer reference.

The invoicing service was written in Python and read the response as a dictionary. A missing key gave it `None`, and it carried on. Nothing failed. Nobody was careless here. The spec existed, but nothing forced the consumer to agree with it, and the spec lived in a YAML file that people updated when they remembered to.

We had a version of this problem every few months. Sometimes the field was renamed, sometimes a number turned into a string, sometimes an optional field became required. Each time the fix was small and the diagnosis took a day.

## What we tried first

Before changing protocols we tried to fix the contract problem inside REST. We generated clients from the OpenAPI specs and added a linter for breaking changes. This helped, and I would not call it a failure. But the generated clients varied a lot in quality between languages, and the Python generator we used produced code nobody wanted to read. Teams started writing their own thin clients by hand, which brought back the drift we were trying to remove.

The underlying issue was that the spec was optional. With REST you can build a service without ever writing one, and for a service nobody else is watching, people did exactly that.

## What gRPC changed for us

With gRPC the `.proto` file is the thing you write first, and the server and client code come out of it. There is no hand-maintained second description to fall out of date. If you want to change a field, you change the proto, regenerate, and the compiler tells you every place that breaks.

We put the protos in one repository and ran `buf breaking` in CI against the main branch. Renaming a field is allowed in the wire format, since protobuf identifies fields by number, but removing one or reusing a number is flagged and blocks the merge. The billing rename would have been a one-line diff that every consumer saw in its own build.

Deadlines were the second thing. In our REST setup, every service picked its own timeout, and when a request fanned out across four services, the outer caller would give up while the inner ones kept working on a response nobody wanted. gRPC passes a deadline along with the call and cancels the downstream work when it expires. We had been meaning to build something like that ourselves for a long time. Getting it by default removed a class of load we had never fully explained.

## What did not change much

I should be honest about performance, because it is the reason people usually give and it was not ours. Our latency barely moved. Protobuf payloads are smaller and parsing is cheaper, and one service that shipped large lists did show a visible drop in CPU. For most of our traffic the time is spent in databases and in queues, and the serialization format is a rounding error.

If you are considering gRPC mainly to go faster, measure first. You may find the gain is small enough that it does not justify the work.

## What it cost

Debugging got worse before it got better. You cannot paste a request into a browser or run `curl` against an endpoint. We turned on server reflection in non-production environments and everyone learned `grpcurl`, but there was a stretch of weeks where people missed being able to just look at the traffic.

Load balancing surprised us. gRPC keeps a single HTTP/2 connection open and sends many calls over it, so a standard connection-level balancer sent all of one client's calls to one backend. We saw one pod pinned at high CPU while its neighbours idled. We fixed it by moving to client-side balancing through our service discovery, which took a sprint we had not planned for.

There is also the matter of people. Writing protos is a skill, and the first ones we wrote were bad. We used a single giant message type for a request that should have been three small ones, and we learned that adding an enum value can break old consumers that do not expect it. We now have a short style document and a review rule that every new proto gets read by someone who did not write it.

## What we left alone

Our public API is still REST. Outside customers use browsers and scripts, and for them JSON over HTTP is the correct tool. Some internal services that are called rarely, such as a nightly report trigger, also stayed on REST because converting them would have cost more than the problem they caused.

We ended up with a gateway that translates between the two for the few places they meet. It is more code than I would like and it has a small bug backlog of its own.

## Where we are now

The migration took about seven months, done one service at a time, with the old REST endpoints kept alive until the last consumer moved. In the time since, we have had no incident caused by a field mismatch between two internal services. We have had plenty of other incidents, so the on-call rotation has not gotten quieter, but the cause of the empty customer reference no longer exists.

If I did it again I would write the style guide and set up the breaking-change check before converting the first service, not after the tenth.
