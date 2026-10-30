---
title: "How Comparing Two Heap Snapshots Found Our Node.js Memory Leak"
published: false
description: "Our Express API was OOM-killed every 3 days for a month. One heap snapshot didn't show the cause. Comparing two did."
tags: node, javascript, debugging, performance
cover_image: [TODO]
---

For a month, one of our Node.js APIs was killed by Kubernetes every three days.

The pattern was always the same. After a deploy or restart, the pod sat at about **300 MB**. Over roughly three days it grew slowly and steadily to **1.8 GB**. Then it hit its memory limit, Kubernetes OOM-killed it, a fresh pod started, and the cycle began again.

[TODO: Grafana/metrics screenshot of the sawtooth memory graph]

This post covers how we found the cause. The main lesson: **don't stare at one heap snapshot. Take two and compare them.**

## The setup

- Express on Node 20
- Running in Kubernetes behind a load balancer [TODO: which LB, e.g. AWS ALB / NGINX ingress]
- A small custom metrics middleware that, among other things, reported the number of active connections

That last item turned out to be the problem.

## Why one heap snapshot isn't enough

A heap snapshot of a healthy, busy Node process is full of objects: strings, closures, buffers, compiled code, framework internals. When you open a single snapshot you see big numbers everywhere, and nothing says which of them is the leak and which is normal.

A leak is something that **grows over time**. To see growth you need two points in time.

So we took two snapshots from the same running process, **6 hours apart**, and loaded both into Chrome DevTools.

[TODO: how you captured the snapshots in the pod, e.g. `--heapsnapshot-signal`, `v8.writeHeapSnapshot()`, an inspector port-forward, and how you copied the files out]

> Tip: writing a heap snapshot pauses the process and needs a lot of extra memory. On a pod that's already near its limit, capturing the snapshot can be what triggers the OOM kill. Take the first one early, and do it on a pod you can afford to lose.

## Reading the comparison

In the DevTools **Memory** tab:

1. Load both `.heapsnapshot` files.
2. Select the **later** snapshot.
3. Change the view from **Summary** to **Comparison**, with the earlier snapshot as the baseline.
4. Sort by **# Delta** or **Size Delta**.

Most constructors barely changed between the two snapshots. One stood out: **tens of thousands of new `Socket` objects** were still retained.

[TODO: screenshot of the Comparison view showing the Socket delta]

Clicking one of those sockets and opening the **Retainers** panel showed what was keeping it alive: an array inside our metrics middleware.

## The bug

The middleware counted active connections by keeping the sockets themselves. Simplified, it looked like this:

```js
// Simplified version of the buggy middleware
const activeSockets = [];

app.use((req, res, next) => {
  const socket = req.socket;
  activeSockets.push(socket);

  socket.on('close', () => {
    const i = activeSockets.indexOf(socket);
    if (i !== -1) activeSockets.splice(i, 1);
  });

  metrics.gauge('active_connections', activeSockets.length);
  next();
});
```

[TODO: replace with the real (sanitized) code if it differs meaningfully]

The idea was to add each request's socket to the array and remove it when the socket closes.

The problem is keep-alive. The load balancer keeps connections open and reuses them for many requests. Those sockets never emitted `'close'` while the process was running, so they were never removed from the array. Every one of them stayed in memory, along with everything it referenced, until the pod died.

[TODO: double-check this explanation. The fix below also relies on each socket's `'close'` event. If `'close'` really never fired, the new counter would drift upward too. Possible factors worth confirming and mentioning: the per-request push, which adds the same reused socket to the array many times while `'close'` removes only one entry, and the per-request `'close'` listener, which piles up listeners on reused sockets.]

## The fix

We stopped holding references to the sockets. We count connections at the server level and keep only a number:

```js
// Simplified version of the fix
let activeConnections = 0;

server.on('connection', (socket) => {
  activeConnections++;
  socket.on('close', () => {
    activeConnections--;
  });
});

// report activeConnections from the metrics endpoint/interval
```

[TODO: real snippet / link to the PR]

The whole change was **12 lines**. The key difference is that we no longer keep any references to the sockets. Even if the count were ever wrong, the worst outcome is an inaccurate number on a dashboard. A socket can no longer be kept in memory by our code.

## The result

Since the fix shipped, memory has stayed **flat at about 320 MB for two weeks**. There have been no more OOM kills.

[TODO: before/after memory graph]

## Takeaways

1. **Compare two heap snapshots.** One snapshot shows what's in memory. Two snapshots show what's *growing*, and growth is what a leak is. Take them far enough apart that the leak has had time to grow. For us, 6 hours was plenty.
2. **Use the Retainers panel.** Once Comparison shows *what* is growing, Retainers shows *who* is holding it. That leads you straight to the code.
3. **Don't store objects just to count them.** If you only need a number, keep a number. Arrays, Maps, and Sets of long-lived objects like sockets, requests, and connections are an easy way to leak memory.
4. **Keep-alive changes connection lifetimes.** Behind a load balancer, one socket can carry a huge number of requests and stay open far longer than you'd expect. Code that assumes "one request, one socket, closes soon" will break.

If your Node service has a sawtooth memory graph, take two snapshots a few hours apart and look at the delta.