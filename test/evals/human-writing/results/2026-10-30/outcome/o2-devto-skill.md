```markdown
---
title: Our Node API leaked sockets for a month. Comparing two heap snapshots found it.
published: false
tags: node, javascript, debugging, kubernetes
---

For a month, our Node API died every three days. Memory climbed from about 300 MB to 1.8 GB, Kubernetes OOM-killed the pod, and the climb started again on the new one.

**TL;DR:** our metrics middleware held on to sockets from the load balancer's keep-alive connections and never released them. Comparing two heap snapshots taken six hours apart pointed straight at it, and the fix changed 12 lines.

## The setup

The service is an Express app on Node 20, running in Kubernetes behind a load balancer. [TODO: rough traffic numbers if you're cleared to share them; an order of magnitude is enough]

[TODO: is the 300 MB → 1.8 GB figure RSS or heapUsed? And anything you checked or suspected before reaching for heap snapshots]

## Why one snapshot isn't enough

[TODO: did you take a single snapshot first? If yes, say what it looked like. If not, keep the paragraph below as general advice.]

A snapshot of a busy server is full of objects that are supposed to be there. Taken early, while a slow leak is still small, the leaked objects are easy to miss among them. The Comparison view lists only what was allocated and freed between two snapshots, so something that keeps accumulating is much easier to spot.

## Comparing two snapshots

We took two snapshots six hours apart [TODO: confirm both came from the same pod/process], loaded both into Chrome DevTools and opened the Comparison view ([DevTools docs](https://developer.chrome.com/docs/devtools/memory-problems/heap-snapshots)).

[TODO: how you captured the snapshots from the pod (`--inspect` bound to 127.0.0.1 and reached through `kubectl port-forward`, `v8.writeHeapSnapshot()`, or `--heapsnapshot-signal`) and how you copied them off, as a `bash` block with placeholder pod/namespace names]

The snapshots showed tens of thousands of retained `Socket` objects [TODO: was that the delta between the two snapshots, or the total in the second one?], held by an array in our own metrics middleware.

[TODO: how you traced them to the array (the Retainers panel?) and what you sorted or filtered by in the Comparison view]

## The bug

The middleware existed to report how many connections were active. It did that by pushing each request's socket into an array and removing the socket when its `'close'` event fired.

Our load balancer keeps connections alive and reuses them, and those sockets were never removed from the array. [TODO: explain exactly why. Your notes say `'close'` never fired for them, but readers will push back: keep-alive sockets do emit `'close'` when they finally end, and the fix below also counts on a close event. Check the original code: which object the listener was on (`req`, `res` or `req.socket`), and whether the array held many distinct sockets or the same socket pushed once per request and removed only once.]

[TODO: the original middleware, trimmed to the relevant lines, with internal names removed]

## The fix

We stopped storing sockets. The server now keeps a plain integer: increment it on the server's `'connection'` event, decrement it when the connection ends. Nothing keeps a reference to the socket itself, so even a missed decrement would skew a number instead of holding memory.

A sketch of the shape (not our exact code):

​```js
const server = app.listen(3000);
let activeConnections = 0;

server.on('connection', (socket) => {
  activeConnections++;
  socket.on('close', () => {
    activeConnections--;
  });
});
​```

[TODO: confirm which event the real fix decrements on (your notes say "the server's 'close' event for each socket"), then replace the sketch with the real diff, 12 lines changed]

## Two weeks later

Memory has stayed flat at about 320 MB for two weeks. [TODO: confirm nothing else shipped in the same deploy]

If you're chasing a slow leak, take one snapshot, wait until the leak has visibly grown (six hours was plenty for ours), take a second, and compare the two in DevTools.

Before you do that in production, know what it costs. Writing a snapshot is synchronous, blocks the event loop while it runs, and needs roughly twice the heap's size in memory ([Node docs](https://nodejs.org/api/v8.html#v8writeheapsnapshotfilenameoptions)). A pod already close to its limit can get OOM-killed by the snapshot itself, and a long pause can fail its liveness probe. Take snapshots early in the climb, on a pod you can afford to lose. A snapshot also holds whatever was in the heap, so treat the file like production data and delete it when you're done.
```