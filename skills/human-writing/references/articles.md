# Articles and posts

How to shape a piece once the material is in (`interview.md`). The voice profile, if there is one, overrides anything here.

## Pick a structure that fits the material

Don't force material into a template. Look at what the interview produced and pick the shape it already has.

| Structure | Use when you have | Shape |
|---|---|---|
| **Story-led** | A concrete moment with stakes | The moment → what you believed → what happened → what you changed → what the reader can take |
| **Build log / postmortem** | A project or incident with numbers | Goal and constraint → the timeline of decisions, each with why → result in numbers → what you'd do differently |
| **Opinion with receipts** | A position some peers reject | The claim in one sentence → the strongest version of the other side → your evidence → where you'd be wrong |
| **How-to** | A repeatable procedure | Who it's for and what they'll have at the end → steps, one action each → how to check it worked → what goes wrong |
| **Comparison** | Two or more options you have actually used | The decision the reader faces → criteria that matter for them → each option against the criteria with evidence → a recommendation with conditions |
| **Notes / list** | Several independent findings | One finding per item, each with its own evidence. Number them only if the count is real ("7 things we cut"), not for rhythm |

A piece can switch shape: a build log often contains an opinion. It must not switch for decoration.

## Openings

The first two sentences decide whether anyone reads the third. Open with one of:

- **The moment**: `On 14 March our nightly export ran for nine hours and blocked the release.`
- **The number**: `We cut our CI bill from $4,100 to $1,350 a month without removing a single test.`
- **The reversal**: `For three years I told every new hire to squash their commits. I was wrong, and the bisect logs prove it.`
- **The claim**, stated flat, for opinion pieces: `Most internal tools should not have a UI.`

Don't open with:

- a definition (`Kubernetes is a container orchestration platform…`);
- the state of the world (`In today's fast-paced…`, `AI is changing everything`);
- a question (it delays the point; LinkedIn analytics vendors also report lower reach for question openers, though LinkedIn has published nothing on it);
- a summary of what the article will do (`In this post I'll walk through…`). If a roadmap helps in a long tutorial, put it after the hook, in one sentence.

## The middle

- **One idea per paragraph**, with its evidence in the same paragraph. A paragraph that only asserts gives the reader nothing to check.
- **Show the mechanism.** "It got faster" is a claim; "the query stopped scanning the whole table because the new index covers `(tenant_id, created_at)`" is an explanation the reader can reuse.
- **Concrete over abstract**, every time you have the choice: the error message, the config line, the command, the name of the meeting.
- **Code in articles**: short, runnable, language-tagged, with the output when the output is the point. Cut boilerplate and link to the full version.
- **Admit the cost.** Every real decision had a downside. Saying what it was makes the rest credible.
- **Section headings** only when the piece is long enough to navigate (roughly 800+ words) and each section has several paragraphs. A heading should name the content (`Why the index didn't help`), not tease it (`The Twist`).

## Endings

End on the last concrete thing: the result, the next step, the open problem, or the question you actually want answered. Good endings are often one plain sentence.

Don't end with:

- a recap of the piece;
- an aphorism (`Because in the end, code is about people.`);
- a generic call to action (`What do you think? Let me know in the comments!`). A specific question works: `If you run Postgres on ARM, did you see the same regression after 16.2?`

## Titles

Write 2–3 options and let the author pick. A good title says what the reader gets or what happened, with a specific noun.

- `How we cut CI time from 41 to 9 minutes without dropping tests`
- `Squash merges hid three regressions from our bisects`
- `What 200 support tickets taught us about our error messages`

Avoid formula titles: `The Ultimate Guide to…`, `Unlocking…`, `…: A Deep Dive`, `Why X Matters More Than Ever`, `Everything You Need to Know About…`. Avoid clickbait the article doesn't pay off.

## Posts (LinkedIn and similar)

A post is not a short article. It is one idea, one story or one number, read on a phone between other things.

- The first line carries the post. It has to work before the "see more" cut (`platforms.md`).
- One idea. If you have three, you have three posts.
- Short paragraphs, one to three sentences, with blank lines between them. That layout is native to the platform and not a tell by itself. The tell is the staccato fragment stack.
- A real detail every few lines: a number, a name, a date, a thing someone said.
- At most one contrast and one triad in the whole post. No `The result?` or `Here's what happened:` bridges.
- End with the takeaway, or a specific question if you want comments. A `P.S.` with a genuine follow-up is fine.
- No external link in the body if reach matters; put it in the first comment and say so.

## Voice without a profile

When there is no voice profile, aim for how a competent engineer talks to a colleague they respect:

- first person where the author did the thing ("we migrated", "I was wrong");
- contractions;
- plain verbs;
- opinions stated as opinions;
- humour only if it arises from the material.

Don't manufacture personality with exclamation marks, "honestly", or confessions the author didn't make.
