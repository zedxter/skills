# Gathering material

A text is only as specific as what the writer knows. This step gets the specifics out of the author, or out of the repository, before any drafting. Ask in one message, conversationally, and only for what you can't infer. Ask for a few things and get them in detail; a long questionnaire gets short answers.

## Articles and posts

Ask for these five things, in this order. Each one becomes a line of the piece's spine.

1. **The moment.** "When did this last actually happen? What exactly happened?" You want a scene with a date, a place or system, and people. Not "teams often struggle with X" but "on 14 March our nightly export took 9 hours and blocked the morning release."
2. **The number.** "What did it cost, save, take, break? How much, how many, how long?" One real number does more than any adjective. Keep the number as precise as the author gave it; never round it up into something rounder or invent decimals.
3. **What you got wrong.** "What did you believe before that you don't believe now?" This is usually the opening beat. It gives the piece tension and makes the author credible.
4. **Who disagrees.** "Who would push back on this, and what would they say?" This names the audience and gives the piece something to argue with. For an opinion piece, if nobody would disagree, the point is probably too obvious to publish. A how-to or build log doesn't need an opponent.
5. **What the reader should do differently.** "After reading, what should someone change on Monday?" This becomes the close.

Then settle the frame:

- **Reader**: who exactly, and what they already know ("backend engineers at startups who have used Postgres but never tuned it").
- **Platform and length**: see `platforms.md`.
- **Goal**: comments, saves, sign-ups, hiring, or understanding. It changes the ending.

### Pressing vague answers

Press each soft answer **once**, then accept what you get and use a `[TODO]` for the gap.

| They say | You ask |
|---|---|
| "recently", "a while ago" | "Roughly which month?" |
| "a lot", "significantly", "much faster" | "From what to what?" |
| "everyone", "most teams" | "Which teams have you seen do this?" |
| "it was hard" | "What was the moment you nearly gave up?" |
| "we improved the process" | "What did you stop doing? What did you start?" |
| "it's best practice" | "What happened when you didn't do it?" |

If the user asks you to write it straight away, or gives no material at all, write a draft with `[TODO: the number]`, `[TODO: when this happened]` in the places a specific detail belongs, and list them at the end. Never fill them with plausible inventions.

### Material from other sources

- **Notes, transcripts, voice memos**: mine them for the five things above. Keep the author's own phrasing when it's vivid; that is voice.
- **Links the user gives**: read them, and put every fact you take into the ledger with its URL. Quote sparingly (a sentence, attributed); summarise the rest in your own words.
- **Research you do yourself**: only with the user's go-ahead, and every fact is sourced in the ledger. Prefer primary sources (the vendor's changelog, the paper, the RFC) over articles about them.

## Documentation

Read first, ask second. The repository answers most questions better than the user's memory.

**Read:**
- the code paths the doc covers, including entry points, argument parsing, config loading and error messages;
- `--help` output for every command the doc mentions (run it if you can);
- config schemas, OpenAPI or GraphQL schemas, type definitions;
- existing docs, the README, the changelog, recent commits touching the area;
- tests, which show real usage and edge cases.

**Then ask only for what the code can't tell you:**

1. **Reader and starting point.** "Who reads this, and what have they already done?" (installed it, never seen it, migrating from v1).
2. **Their goal.** "What are they trying to get done when they open this page?" This decides the Diátaxis type (`docs.md`).
3. **Scope.** "What should this page deliberately not cover?"
4. **Environment.** Supported OS, versions, required access, and anything the code assumes silently.
5. **Known pain.** "Where do people get stuck today? What do they ask in issues or chat?" The answers belong in the troubleshooting section.

## The facts ledger

Start it during the interview and keep it next to the draft. One line per factual claim the text makes:

```markdown
| # | Claim (as written) | Source | Status |
|---|---|---|---|
| 1 | nightly export took 9 hours on 14 March | user | given |
| 2 | 12 minutes after the change | user | given |
| 3 | `--batch-size` defaults to 500 | src/cli.py:88 | verified |
| 4 | Postgres 16 added `pg_stat_io` | https://www.postgresql.org/docs/16/release-16.html | verified |
| 5 | migration takes about 10 minutes for 1M rows | — | TODO: ask |
| 6 | `flowctl sync` prints "3 files synced" | ran in /tmp/scratch | tested |
```

- **Status**: `given` (the user said it), `verified` (checked against a file or page), `tested` (you ran it), `untested` (a command you could not run), `TODO` (missing; the text carries a `[TODO]`).
- Opinions and the author's judgements don't go in the ledger; facts do. "Postgres is the right default" is an opinion. "Postgres handled 4,000 writes a second on our db.m6g.large" is a fact.
- The ledger goes to the fact checker during review and to the user with the final text.
