# Before and after

Each pair shows a typical chat-model draft and a rewrite. The rewrites use facts that a real interview would have produced; those facts are invented for the example. In real work, a fact you don't have becomes a `[TODO]`, never an invention.

## LinkedIn post

Before (lint: 43/100, faux-insight error, 5 hashtags):

~~~text
I got promoted to Staff Engineer today. 🎉

But here's what nobody tells you about getting promoted:

It's not about writing more code.
It's about multiplying the impact of others.

The real growth happened when I started:

✅ Mentoring junior engineers
✅ Writing design docs that unblocked teams
✅ Saying no to the wrong projects

The lesson? Your impact is measured by the success of the people around you.

#leadership #engineering #growth #career #softwareengineering
~~~

After (material from the interview: the design doc, the number of teams, the project they declined):

~~~text
In 2025 I merged 41 pull requests. In 2023 it was 380. Today I was promoted to Staff Engineer.

Most of this year went into one design doc: how three teams should share a single event schema instead of keeping their own. It took six weeks and 140 comments. After it landed, the teams stopped breaking each other's consumers, which had happened about twice a month.

The hardest part was turning down the search rewrite in March. It was the project I wanted most, and nobody else could have staffed the schema work.

If you're on the same path: count what other teams stop getting stuck on, not what you ship.
~~~

What changed:

- the hook is a number;
- the triad of virtues became one story with its cost;
- the hashtags, emoji bullets and the `here's what nobody tells you` setup are gone;
- the ending is a concrete rule the reader can apply.

## dev.to article opening

Before:

~~~markdown
# Unlocking Performance: My Journey From Python to Rust

Every developer reaches a point where their favorite tool is no longer enough. Python had served us well. It was flexible, expressive, and easy to learn. But as our dataset grew, so did our latency — and our cloud bill.

Enter Rust.
~~~

After:

~~~markdown
# Rewriting one Python stage in Rust cut our pipeline from 3 hours to 25 minutes

Our nightly pipeline parses about 40 GB of gzipped JSON logs. By August it ran for three hours, and the parse stage took two of them. We rewrote that stage in Rust and left the rest in Python. The whole run now takes 25 minutes, and the instance bill dropped by about a third.

**TL;DR:** profile first. Only one stage was CPU-bound; rewriting the other five would have bought nothing.
~~~

What changed:

- the title says what happened;
- the opening gives the workload and the numbers;
- "my journey" and "Enter Rust." are gone;
- the TL;DR carries the transferable lesson.

## README section

Before:

~~~markdown
## ✨ Features

- 🔄 **Real-time sync:** Keep your databases in perfect harmony.
- ⚡ **Blazing fast:** Optimized for performance at scale.
- 🛡️ **Secure by default:** Your data is always protected.
~~~

After:

~~~markdown
## What it does

`datasync` copies changed rows from one Postgres database to another, using logical replication, usually within a second of the commit. It handles schema changes that add columns; renames and drops need a manual step (see *Schema changes*).

On a 4-vCPU machine it keeps up with about 6,000 row changes per second. Connections use TLS, and credentials are read from environment variables, never from the config file.
~~~

What changed:

- the emoji, bold labels and adjectives became the mechanism, a measured limit and the actual security behaviour;
- the limitation is stated up front.

## How-to step

Before:

~~~markdown
Simply open the settings and easily configure your webhook. It's that simple!
~~~

After:

~~~markdown
1. Open **Settings → Integrations → Webhooks** and click **Add webhook**.
2. In **Payload URL**, paste the URL of your endpoint, for example `https://example.com/hooks/acme`.
3. Under **Events**, select **Order created**.
4. Click **Save**. A test event is sent right away; your endpoint should log a `200`.
~~~

What changed:

- the minimizers are gone;
- there is one action per step, with the exact UI path;
- the reader learns how to check it worked.
