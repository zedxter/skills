---
title: Our Node API leaked sockets for a month. Two heap snapshots found it.
published: false
tags: node, javascript, debugging, kubernetes
---

For a month, our Node API died every three days. Memory climbed from about 300 MB to 1.8 GB, Kubernetes OOM-killed the pod, a fresh one started, and the clock reset.

**TL;DR:** a metrics middleware stored sockets in an array to count connections, and the array kept growing because entries were never removed. I found it by comparing two heap snapshots taken six hours apart, not by staring at one. The fix was a 12-line change from an array to an integer.

The service is an Express app on Node 20, running in Kubernetes behind a load balancer. [TODO: how you first noticed the three-day pattern, and the pod's memory limit if you're allowed to share it]

## One snapshot told me nothing

[TODO: what you tried first, if anything, before the two-snapshot comparison]

Before taking any snapshot, check that the V8 heap is what's growing. A heap snapshot only covers V8's heap, and the memory Kubernetes measures also includes native memory. `process.memoryUsage()` reports both: `heapUsed` for V8, `rss` for the whole process ([docs](https://nodejs.org/api/process.html#processmemoryusage)). [TODO: did you check this, and what did it show?]

A single heap snapshot of a Node process is a long list of strings, closures, arrays and objects that belong to Express and Node. Almost all of it is supposed to be there. A leak is the part that keeps growing, and growth doesn't show up in one picture.

### Taking the snapshots

[TODO: how you captured the snapshots from the pod and copied them off it, with placeholder names like `my-namespace` and `my-pod`]

Three warnings if you do this in production. Node's docs say that writing a heap snapshot needs about twice the current heap size in memory and blocks the event loop while it runs ([v8.writeHeapSnapshot](https://nodejs.org/api/v8.html#v8writeheapsnapshotfilenameoptions)). On a pod that is already near its limit, the snapshot itself can be what gets you OOM-killed, so take the first one early, while the heap is still small. A blocked event loop can also fail the pod's liveness probe, so expect Kubernetes to notice. And a snapshot holds everything that was in memory, including tokens and user data, so treat the file like a secret and delete it when you're done.

### Comparing them

I took two snapshots six hours apart and opened both in Chrome DevTools. In the Memory panel you can select the later snapshot and view it as a comparison against the earlier one, then sort by how many objects of each type were added between the two.

The `Socket` row stood out, with tens of thousands of new objects that were still retained, meaning something still referenced them and the garbage collector couldn't free them. Following what was holding them led to an array inside our own metrics middleware.

[TODO: screenshot of the comparison view with the Socket row, cropped to the type, count and size columns, and checked for hostnames, IPs and tokens]

## The bug

The middleware counted active connections. For every request it pushed the request's socket into an array, and it removed the socket when the socket emitted `'close'`.

The load balancer reuses connections for request after request (HTTP keep-alive), and on those connections the entries were never removed. As long as the array held the sockets, the garbage collector couldn't free them or anything they referenced.

[TODO: confirm the exact mechanism before publishing. Your notes say `'close'` never fires for keep-alive sockets, but the fix below relies on the socket's `'close'` event, Node's docs say a socket emits `'close'` once it is fully closed, and Node 20 closes idle keep-alive sockets after `server.keepAliveTimeout`. Did the same socket get pushed once per request and removed only once? Did the retainer chain show tens of thousands of distinct sockets, or many entries pointing at the same few? Was `keepAliveTimeout` changed? Update the TL;DR to match.]

[TODO: the original middleware code, trimmed to the lines that matter, with internal names changed]

## The fix

We don't need the sockets, only how many there are. The new code keeps an integer that goes up on the server's `'connection'` event and down when that socket emits `'close'`. Each socket is counted once per connection rather than once per request, and nothing holds a reference to it.

Simplified, the shape is:

```javascript
const express = require('express');

const app = express();
const server = app.listen(3000);
let activeConnections = 0;

server.on('connection', (socket) => {
  activeConnections++;
  socket.on('close', () => {
    activeConnections--;
  });
});
```

[TODO: replace with the real 12-line diff if you can share it, and confirm it decrements on each socket's `'close'`; your notes say "the server's 'close' event for each socket"]

Memory has stayed flat at about 320 MB for two weeks since the deploy. [TODO: confirm the deploy contained only this change]

[TODO: memory graph before and after, with cluster, namespace and dashboard names cropped out]

## What I'd do first next time

If `heapUsed` grows slowly and steadily, take two heap snapshots several hours apart and compare them before reading either one closely. The comparison shows what was added and survived. The leak is in there, mixed with some legitimate growth like warming caches; if you can't tell them apart, an optional third snapshot shows which type keeps growing.

If all you need is how many connections are open, keep an integer or call [`server.getConnections()`](https://nodejs.org/api/net.html#servergetconnectionscallback). If you do need the socket objects, for graceful shutdown for example, track them per connection and remove each one when it closes.