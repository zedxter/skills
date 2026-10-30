---
title: "How Comparing Two Heap Snapshots Found Our Node.js Memory Leak"
published: false
description: "Our Express API got OOM-killed every 3 days. One heap snapshot didn't explain it. Comparing two did."
tags: node, javascript, debugging, performance
cover_image: [TODO: cover image URL]
---

For a month, our Node.js API died every three days.

The service runs Express on Node 20 in Kubernetes. After each restart it used about **300 MB** of memory. Over the next three days that number crept up until it reached **1.8 GB**, and then Kubernetes OOM-killed the pod. The pod restarted, the count reset, and the cycle began again.

[TODO: screenshot of the memory graph showing the sawtooth pattern]

In the end the bug was 12 lines of code. The more useful thing I learned was how to find it: **compare two heap snapshots instead of staring at one.**

## Why one snapshot isn't enough

A single heap snapshot of a real application is huge. You get thousands of object types and millions of objects. Most of them are supposed to be there: caches, compiled code, framework internals, connection pools. Nothing in one snapshot labels an object as "should have been freed." You're looking for something that grows inside a picture that doesn't move.

A leak is about **change over time**. So capture the heap at two moments and ask a different question: *what grew in between?*

## Taking the snapshots

We took two heap snapshots from the same running process, **6 hours apart**, while it was under normal traffic.

[TODO: how we captured the snapshots in production, e.g. `--heapsnapshot-signal`, `v8.writeHeapSnapshot()`, or the inspector, plus any gotchas with doing it inside Kubernetes]

For reference, Node 20 gives you a few ways to do this:

- Start the process with `node --heapsnapshot-signal=SIGUSR2 app.js` and send `kill -USR2 <pid>` whenever you want a snapshot.
- Call `require('v8').writeHeapSnapshot()` from code, for example behind an internal admin endpoint.
- Attach Chrome DevTools via `--inspect` and take one from the Memory tab.

One warning: taking a snapshot pauses the process, and it can temporarily need a lot of extra memory. Be careful on a pod that is already near its limit.

## Comparing them in Chrome DevTools

1. Open Chrome DevTools, go to the **Memory** tab, and load both `.heapsnapshot` files.
2. Select the **second** (later) snapshot.
3. Change the view dropdown from **Summary** to **Comparison** and compare against the first snapshot.
4. Sort by **# Delta** or **Size Delta**.

Most of the noise disappears. Things that are allocated and freed at a steady rate show a delta close to zero. What's left at the top is what accumulated.

In our case the top entry was obvious: **tens of thousands of new `Socket` objects** that were still retained.

[TODO: screenshot of the Comparison view showing the Socket delta]

Next, click one of those `Socket` objects and open the **Retainers** pane below. It shows the chain of references keeping the object alive. Every one of these sockets was held by **an array in our metrics middleware**.

## The bug

We had a middleware that tracked the number of active connections. It worked roughly like this:

- On every request, push the request's socket into an array.
- When the socket emits `'close'`, remove it from the array.
- Report `array.length` as "active connections."

This looks reasonable, but our sockets are **keep-alive connections that the load balancer reuses**. For those sockets, `'close'` never fired, so they were never removed from the array. Each socket, along with everything it referenced, stayed reachable forever. Over three days that added up to 1.5 GB.

Here is a simplified sketch of the pattern (not our exact code):

```js
// ❌ Leaky: keeps a reference to every socket it sees
const activeSockets = [];

app.use((req, res, next) => {
  const socket = req.socket;
  activeSockets.push(socket);

  socket.on('close', () => {
    const i = activeSockets.indexOf(socket);
    if (i !== -1) activeSockets.splice(i, 1);
  });

  next();
});

// metrics: activeSockets.length
```

[TODO: replace with the actual original middleware code if you want to show it]

## The fix

We didn't need the sockets themselves. We only needed a **number**. So we stopped storing references and kept a plain integer instead. It is incremented on the server's `'connection'` event and decremented on `'close'` for each socket:

```js
// ✅ Fixed: counts connections without holding references to them
let activeConnections = 0;

server.on('connection', (socket) => {
  activeConnections++;
  socket.once('close', () => {
    activeConnections--;
  });
});

// metrics: activeConnections
```

[TODO: replace with the actual diff, which was 12 lines changed]

Two things changed:

- **Counting connections at the server level** instead of per request in middleware. A connection is counted once, when it opens, no matter how many requests run over it.
- **Keeping no references.** An integer can't retain a `Socket`. Even if the count were ever off, the worst result would be a wrong metric, not a process that eats 1.8 GB and gets killed.

## The result

Since the fix shipped, memory has stayed **flat at about 320 MB for two weeks**. No more OOM kills, and no more restarts every three days.

[TODO: screenshot of the memory graph after the fix]

## Takeaways

- **Compare two heap snapshots; don't stare at one.** Take them far enough apart for the leak to show, then use the Comparison view and sort by delta. Whatever grew is your suspect.
- **Follow the Retainers pane.** Knowing *what* leaked is half the answer. The retainer chain tells you *who is holding it*, and that usually points straight at the code.
- **Be wary of collections that hold long-lived objects "just to count them."** If you only need a number, store a number.
- **Don't assume lifecycle events fire when you expect.** Keep-alive connections behind a load balancer can live much longer than you'd think, and cleanup code tied to `'close'` may run much later than you planned, or never.

If you have a Node service whose memory slowly climbs between restarts, take two snapshots a few hours apart and compare them. It will probably save you a lot of time.

[TODO: closing line / link to your profile, team, or company]