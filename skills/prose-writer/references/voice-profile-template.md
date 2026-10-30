# Voice profile template

Fill this from 3–6 texts the author wrote themselves: posts, articles, long messages, talk transcripts. Texts already edited by a model teach the model's voice back, so skip them. Quote real phrases as evidence; a profile of adjectives ("friendly, professional") is useless.

Save the result where the user chooses, by default `voice-profile.md` in their project. Don't save it inside the skill's folder; updates overwrite that folder.

```markdown
---
author: <name or handle>
built_from: <list of the sample texts, with dates>
updated: <YYYY-MM-DD>
---

# Voice profile: <name>

## In one paragraph
How this person sounds to a reader who knows them: what they care about, how they argue,
how much of themselves they put in. Two or three sentences.

## Sentences
- Typical length and spread: e.g. "mostly 10–18 words, with one long sentence per paragraph"
- Openers they use: e.g. starts paragraphs with "So," and "Look,"; uses "And" and "But" to start sentences
- Fragments: never / occasionally for emphasis / often
- Questions: rhetorical? genuine? at the end only?

## Words
- Words and phrases they actually use (quote): "ship it", "boring tech", "footgun"
- Words they never use, or that would sound wrong from them: e.g. "synergy", "journey", "excited to announce"
- Jargon level: e.g. assumes readers know Kubernetes; defines anything below the infra layer
- Profanity, slang, British or American spelling

## Punctuation and format
- Dashes: e.g. uses em dashes freely, two or three per post
- Lists vs prose; emoji (where, how many); bold; headings
- Oxford comma: yes / no

## Stance
- How they disagree: e.g. names the position and the person, then says why
- Hedging: e.g. rarely hedges; says "I think" only when genuinely unsure
- Humour: e.g. dry, self-deprecating, about their own mistakes; never jokes about users
- What they reveal about themselves: failures yes, salary no, family never

## Openings and endings
- How their best pieces open (quote two)
- How they end: e.g. a plain next step, never a question

## Hard rules
- Things the author has said never to do: e.g. "never call it a journey", "no hashtags"

## Linter overrides
Habits the linter would flag that are genuinely theirs. The linter reads these two lines with --voice.

- lint-allow-rules: em-dash
- lint-allow-words: honestly, actually
```

Keep the overrides honest. Allow a rule only if the samples show the habit clearly, not because the draft happens to trip it.
