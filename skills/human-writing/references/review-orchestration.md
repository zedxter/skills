# Review orchestration

The last step before a text goes to the user for publishing. The main agent is the **orchestrator**: it picks the lenses, starts one reviewer per lens, merges the findings, edits the text and writes the report. It does not review the text itself; it wrote or edited it and cannot see it fresh.

## 1. Pick the panel

| Lens (brief in `reviewers/`) | Article | Post | Docs | `quick` |
|---|:-:|:-:|:-:|:-:|
| `cold-reader` | ✓ | ✓ | ✓ | ✓ |
| `hostile-expert` | ✓ | ✓ | ✓ | |
| `fact-checker` | ✓ | ✓ | ✓ | ✓ |
| `slop-hunter` | ✓ | ✓ | ✓ | ✓ |
| `publication-safety` | ✓ | ✓ | ✓ | ✓ |
| `voice-keeper` (needs a voice profile or 2+ author samples) | ✓ | ✓ | – | |
| `platform-editor` | ✓ | ✓ | – | posts and `repurpose` |
| `newcomer-tester` | – | – | ✓ | |
| `source-checker` | – | – | ✓ | |

- `full` (default for articles and docs): every lens marked for the genre, usually 6–7 reviewers.
- `quick` (posts, texts under ~400 words): the lenses in the last column. For posts and for `repurpose`, that includes `platform-editor`, because platform fit is the point of both.
- `repurpose` takes the size of the target: a LinkedIn post gets `quick`, a dev.to article gets `full`.
- The user can add or drop lenses. A docs page with a narrative section (a blog-style release announcement) can add `platform-editor`; an opinion article that walks through commands can add `newcomer-tester`.

## 2. Prepare each reviewer's input

Give every reviewer exactly three things:

1. **The brief**: the full text of `reviewers/<lens>.md`.
2. **The text** under review, as the reader will see it (Markdown with front matter if the platform uses it), with line numbers so findings can point at lines.
3. **Lens inputs**, and nothing beyond them:

| Lens | Extra input |
|---|---|
| `cold-reader` | Audience one-liner ("backend engineers who know Docker but not Kubernetes") and the platform |
| `hostile-expert` | Audience one-liner and the field ("Postgres performance") |
| `fact-checker` | The facts ledger, plus the source files or the text of the source pages (fetched beforehand if the reviewer has no network) |
| `slop-hunter` | The linter's JSON output for this text and the genre |
| `publication-safety` | The platform and who the author is (company employee, independent) |
| `voice-keeper` | The voice profile, or 2–6 texts the author wrote themselves |
| `platform-editor` | The platform, the goal (comments, saves, sign-ups, understanding), the length target |
| `newcomer-tester` | The Diátaxis type, the stated prerequisites, the repository path, and the run policy. Default: dry run only. Allow running commands only when the user has agreed, and only in a scratch directory or container you name |
| `source-checker` | Paths to the code, the `--help` output, the schema or OpenAPI file the doc describes |

Never include the conversation, the interview notes, your own intentions ("I wanted this paragraph to…"), earlier drafts or another reviewer's findings. Independence is the point: a reviewer who knows what the author meant reads that meaning into the text.

### Inputs in `edit` and `review`

When the text comes from the user, there was no interview, so build the inputs before starting the panel:

- **Ledger**: list the factual claims the text makes, each with status `author` (stated by the author, source not given). The fact checker treats `author` claims as needing a source only when they are surprising, precise or about someone else. Missing sources become questions for the author, not blockers.
- **Audience, platform, author role**: infer them from the text and the request; if you can't, ask the user once, in one message, before starting the panel.

## 3. Run

- Start all reviewers at the same time, as separate sub-agents.
- Ask each to answer **only** with the JSON below. A reviewer that returns prose instead gets one retry with the schema repeated; after that, read its prose and convert it yourself.
- Reviewers report problems and suggest fixes. They never return a rewritten text.

### Finding schema

```json
{
  "lens": "fact-checker",
  "verdict": "ship | revise | block",
  "summary": "One or two sentences on the text from this lens.",
  "findings": [
    {
      "severity": "blocker | major | minor",
      "line": 14,
      "quote": "cut deploy time by 80%",
      "problem": "The ledger says 40 min → 12 min, which is 70%, not 80%.",
      "fix": "Say \"from 40 minutes to 12\".",
      "confidence": "high | medium | low"
    }
  ],
  "questions_for_author": ["Is the customer name in paragraph 3 cleared for publication?"]
}
```

Severity means the same in every lens. Every brief carries this block word for word; some add one line that applies it to their lens.

- **blocker**: publishing would cause harm or embarrassment. Examples are a wrong or invented fact, a leaked secret, a broken command in a tutorial, or a claim the source contradicts.
- **major**: the reader is lost, misled or put off. Examples are an unclear main point, a buried hook, a cluster of tells, or a missing step.
- **minor**: worth fixing if cheap. Examples are a weak word, a small rhythm issue, or an inconsistent term.

The `verdict` is the lens's overall call and goes into the report. What you fix, and whether round 2 runs, depends on the severities of the findings, not on the verdicts.

## 4. Merge

1. **Collect** all findings into one list. Keep the lens on each.
2. **Dedupe**: findings on the same quote or overlapping lines with the same problem become one finding with several lenses. Agreement between independent lenses raises priority.
3. **Resolve conflicts** by precedence: publication safety > facts > source of truth (`source-checker`, `newcomer-tester`) > comprehension (`cold-reader`) > platform > voice > style (`slop-hunter`).
   - A `hostile-expert` finding that a claim is wrong belongs to the facts tier, even when the ledger marks the claim `given` or `author`. If you can't settle it from the sources, ask the author. The expert's other findings (missing caveats, weak arguments) sit with comprehension.
   - In practice the order means two things. A habit documented in the voice profile stays even when the slop hunter flags it. A hook that confuses the cold reader gets fixed even when the platform editor likes it.
4. **Decide** each finding:
   - **fix**: blockers always, majors almost always, minors when the fix is one clause;
   - **reject**, with a one-line reason. Typical reasons: low confidence and you can see the reviewer misread, the fix would add a fact you don't have, or the fix would flatten the author's voice;
   - **ask the author**, when only they can settle it: consent to name someone, a missing number, a deliberate stylistic choice.
5. **Edit** the text. Apply fixes in the author's voice, not in the reviewer's wording. A fix that needs a fact you don't have becomes a `[TODO]` or a question, never an invention.
6. **Re-lint** with the same genre and threshold.

## 5. Round 2 and stopping

- If any blocker or major was fixed, re-run **only the lenses that reported one**, on the revised text, with the same isolation rules. They don't see their round-1 findings; they review the new text cold.
- Stop after round 2 at most. Whatever is still open goes into the report as an open question; never start round 3.
- **Over-editing check:** count the sentences the panel's fixes changed, across both rounds, in any mode. If more than ~40% changed, say so in the report. For the author's own text (`edit`, `review`), also offer the lighter version. Heavy editing flattens a voice.

## 6. Single-agent fallback

If the agent can't start sub-agents, run the same lenses one after another yourself. For each pass:

- Start from the brief and the text only. Re-read the text from the top, and set aside what you know about the author's intent as far as you can.
- Write that lens's JSON before starting the next lens.

Mark the report `single-agent`: independence is weaker, so be more conservative before declaring a text ready.

## 7. Report

Return this to the user with the final text:

```markdown
## Review report
Panel: full (6 reviewers: cold-reader, hostile-expert, fact-checker, slop-hunter, publication-safety, platform-editor) · 2 rounds · sub-agents
Lint: 88/100 (article), pass

### Fixed
- [blocker · fact-checker] "cut deploy time by 80%" → "from 40 minutes to 12" (ledger says 70%)
- [major · cold-reader, platform-editor] the point appeared in paragraph 4; moved to the opening

### Rejected
- [minor · slop-hunter] em dash in line 9: the author's documented habit (voice profile)

### Open questions for you
- Paragraph 3 names Acme Corp. Is that cleared?
- [TODO] the latency number for the old setup

### Verdicts
cold-reader: ship · hostile-expert: revise (round 1) → ship · fact-checker: block → ship · …
```

Keep the report short: one line per finding. The user needs to see what changed and what still needs their decision, not the reviewers' reasoning.
