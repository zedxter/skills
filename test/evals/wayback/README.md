# wayback evals

Trigger evals check whether an agent loads the skill when the request is about archived web pages, and leaves it
alone for nearby requests that are not. `prompts.json` has 14 prompts:

- 7 that must load the skill: a dead link, a page that changed, a Russian request, archive this page, old PDFs on
  a site, proof of what a page said, and old docs (in Russian).
- 7 that must not: a local file, a git-tracked file, a backup script, a WARC question, a live URL to summarise,
  git history, and archiving an email thread.

The runner is shared with human-writing:

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill wayback
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill wayback
```

Results from 2026-10-30, with the final description, are in `results/2026-10-30/`:

| Agent | Correct |
|---|---|
| Claude Sonnet | 14/14 |
| Codex | 14/14 |

The runs use read-only tools, so "archive this page" loaded the skill without saving anything.

The script itself is covered offline by `test/test_wayback.py`, which runs against a fake archive. It tests:

- the CDX index, with the real URL-order and regex-filter behaviour;
- redirects, archived error pages and lost captures;
- the Availability API;
- Save Page Now, with and without keys.

`WAYBACK_LIVE=1` adds a read-only smoke test against the real archive with example.com. Save Page Now is never
called live from the tests, because a save publishes a public copy.
