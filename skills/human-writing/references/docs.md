# Technical documentation

Docs are judged by one test: can the reader do or understand the thing, using only this page, the first time? Personality is welcome in small doses, but accuracy and consistency come first.

## Pick the type first (Diátaxis)

Every page serves one of four needs. Mixing them is the most common reason docs feel long and still unhelpful. See diataxis.fr for the full framework.

| Type | The reader is… | The page… | Must not |
|---|---|---|---|
| **Tutorial** | learning, new to the thing | takes them through a complete, safe exercise to a visible result | branch into options, explain theory at length, assume prior setup |
| **How-to guide** | working, with a specific goal | gives the steps to reach that goal, for someone who knows the basics | teach fundamentals, cover every option |
| **Reference** | working, looking something up | describes the machinery: every flag, field, endpoint, error, default | instruct or argue; it states facts, completely and consistently |
| **Explanation** | studying, wants to understand | discusses why: design, trade-offs, history, alternatives | contain step-by-step instructions |

If a page needs two types, split it and link the parts: "To set this up, see *How to configure SSO*."

## Style

- **Second person, imperative** for instructions: `Run the migration.` not `You will want to run the migration` or `The user should run…`.
- **Present tense**: `This command creates a file.` not `will create`. The linter flags `will` in docs as `docs-future`.
- **One term per concept.** Pick "workspace" or "project" and never alternate. If the UI says "Settings", the doc says "Settings". Keep a short glossary for anything ambiguous.
- **No minimizers**: `simply`, `just`, `easy`, `obviously`, `of course`. They are wrong exactly when the reader is stuck (`docs-minimizer`).
- **No "please"** in instructions (`docs-please`), and no cheerleading (`Congratulations!`, `Happy coding!`).
- **Plain words over Latin**: "for example", "that is", "and so on" (`docs-latin`).
- **Sentence-case headings** that name the task or thing: `Configure SSO with Okta`, `Rate limits`.
- **Short paragraphs**, but no fragments. Lists for steps and for parallel items; prose for anything with "because".
- **Say what happens**: after a step the reader can't see the effect of, tell them how to check it.

## Procedures

~~~markdown
## Rotate the API key

Before you start, you need admin access to the workspace and the `acme` CLI 2.4 or later (`acme --version`).

1. Create a new key:

   ```bash
   acme keys create --name ci-2026-10
   ```

   The command prints the key once. Copy it now; you can't see it again.

2. Update the secret in your CI settings to the new key.
3. Trigger a build and check that it passes.
4. Revoke the old key:

   ```bash
   acme keys revoke ci-2026-04
   ```

If the build fails with `401 invalid key`, the CI cache may still hold the old value. Clear the cache and re-run.
~~~

- **Prerequisites first**: access, versions, what must already be installed or configured.
- **One action per step.** If a step has "and then", it's two steps.
- **Every command copy-pasteable**: no prompt characters, no line-wrapping that breaks the shell, placeholders in capitals like `YOUR_API_KEY`, explained right after the block. Avoid `<angle brackets>`: pasted unchanged into a shell, they are redirections.
- **Expected output** after any command whose output matters, trimmed to the relevant lines.
- **Failure path**: the most likely error message, quoted exactly, and what to do.
- **Code blocks** always carry a language tag (`code-fence-lang`).

## Verify against the source

The `source-checker` reviewer does this during review; do it yourself while drafting too.

- **Flags, defaults, env vars, config keys**: read them from the argument parser or schema, not from memory or old docs.
- **Commands**: run them where it's safe (a scratch directory, a container, a dry-run flag). Record `tested` or `untested` in the facts ledger. Never run destructive commands or commands against production to "test" a doc.
- **Versions**: state the version the doc was checked against when behaviour differs between versions.
- **Error messages**: copy them from the code exactly, so readers can search for them.

## Templates

### README

What a newcomer needs, in this order. Skip sections that don't apply; don't pad.

1. **Name and one sentence**: what it does and for whom. No adjectives the code can't prove.
2. **A minimal example**: the shortest path to "it works", with output.
3. **Install**: per platform if it differs.
4. **Usage**: the 3–5 things people actually do, each with a command or snippet.
5. **Configuration**: a table of options or a link to the reference.
6. **Limits and status**: what it doesn't do, stability, supported versions.
7. **Contributing, licence.**

Match the project type:

- **Library**: API first.
- **CLI**: commands first.
- **Internal service**: setup, architecture and runbooks.
- **Personal or config repo**: what's here and why.

No emoji headings, no badge walls before the first sentence, no feature lists in bold-label form.

### ADR (architecture decision record)

```markdown
# ADR-012: Use Postgres logical replication for the analytics copy

Status: accepted · 2026-10-14 · deciders: @anna, @lee

## Context
What forces the decision now: numbers, constraints, deadlines.

## Decision
What we will do, in one paragraph.

## Options considered
Each option with its real cost; why it lost.

## Consequences
What gets easier, what gets harder, what we must now watch.
```

Write the ADR so that someone who joins in a year understands why, including what was rejected.

### Changelog

Follow keepachangelog.com: an `Unreleased` section at the top, then newest version first, each with an ISO date (`## [1.4.0] - 2026-10-30`), and entries grouped under Added, Changed, Deprecated, Removed, Fixed, Security. Each entry is one line a user can act on, written for users and not for the commit log. Link the issue or PR.

### Release notes

Changelog entries say what changed; release notes say what it means for the reader.

- Lead with the change most users will notice.
- Breaking changes get their own section with the migration steps or a link to them.
- Thank external contributors by name.
- Skip internal refactors unless they change behaviour or performance in a way users see.

### Migration guide

1. Who must migrate, and by when.
2. What breaks, listed exhaustively.
3. Steps, in order, with a way to check each one.
4. How to roll back.
5. Known issues.

### Reference (API, CLI, config)

- Consistent entries: the same fields in the same order for every item (name, type, default, required, description, example).
- Complete: every flag, every field, every error code. Readers assume a reference lists everything, so a missing entry reads as "doesn't exist".
- Examples per item where usage isn't obvious, with real values.
- Generated from the source when possible; hand-written prose goes into explanation pages.

### Error messages

Write them in three parts: what happened, why if known, and what to do. Include the value that was wrong. For example: `Config key "timeout" must be a number of seconds, got "30s". Use timeout: 30.` No blame and no apology theatre.
