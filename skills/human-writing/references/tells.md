# AI tells

These are the habits that make prose read as if a chat model produced it. Each entry names the linter rule that catches it, or says `review` when only a reader can. Each entry also says where the habit is legitimate, because every one of these devices turns up in good human writing. What gives a text away is the same device used over and over where it carries no information.

Severity in the linter differs by genre (`lint_prose.py --list-rules`). In docs, repeated structure, headings and lists are the job, so most rhythm rules are off there.

To fix any of them, find the point the sentence was circling and state it with the fact, name or number that proves it. Swapping a flagged word for a synonym keeps the shape, and the shape is what readers notice.

## 1. Reveals instead of statements

The sentence builds suspense for a point it could have made directly.

| Habit | Rule | Before → after |
|---|---|---|
| **Correcting a claim nobody made**: "it's not X, it's Y". | `not-x-but-y` | `The bottleneck isn't the database. It's the network.` → `Most of the 900 ms is spent on the network; the query takes 40.` |
| **Listing what it isn't**, then what it is. | `negative-list` | `Not a framework. Not a library. A set of scripts.` → `It's a set of scripts.` |
| **Label, colon, punchline** in lowercase. | `colon-reveal` | `The surprising part: nobody noticed for a week.` → `Nobody noticed for a week, because the alert went to an archived channel.` |
| **Asking and answering in one breath.** | `rhetorical` | `The result? Faster builds.` → `Builds now take 6 minutes instead of 14.` |
| **Warm-up phrases** before the point. | `throat-clearing` | `Here's the thing: retries hide bugs.` → `Retries hide bugs.` |
| **"What nobody tells you"**, which casts the writer as the only one who knows. | `faux-insight` | `What most teams get wrong is caching.` → `We cached the wrong layer.` |
| **A short sting** closing every paragraph, often an aphorism. | `kicker` | Paragraph … `And that's the whole game.` → stop at the last sentence that carries a fact. |
| **Summing up** what the reader read a minute ago. | `recap-ending` | `In conclusion, monitoring matters.` → end on the next step or the last result. |
| **Rebutting an imaginary opponent**: "some say…" with no one named. | `review` | Argue with a named position or with none. |

Legitimate uses: one sharp contrast where readers really do believe the first half; a question left open for the reader at the end of a post.

## 2. Mechanical rhythm

| Habit | Rule | Notes |
|---|---|---|
| **Threes by default**: three adjectives, three benefits, three clauses. | `triad` | Budget: about one per 250 words. Often one of the three is the real point; keep that one. |
| **Strings of fragments**: `Fast. Cheap. Reliable.` | `fragment-stack` | Write the sentence that says how and why. |
| **Same first word** three sentences running. | `repeated-opener` | Deliberate repetition once in a piece is fine. |
| **Every sentence the same length.** | `uniform-rhythm` | Don't fake variety with random short lines; let an explanation run long and a plain fact stay short. |
| **Dashes everywhere.** | `em-dash` | The budget scales with length; in posts prefer none. Periods, commas, colons and parentheses do most of the work. A documented habit of the author wins. |
| **Piled-up hedges**: `could potentially`, three qualifiers in a sentence. | `hedge-stack` | Keep the one hedge that's true. |
| **Intensifiers**: very, truly, literally, fundamentally. | `intensifier` | The fact carries the weight or nothing does. |

## 3. Puffery and vague authority

| Habit | Rule | Before → after |
|---|---|---|
| **Words that became model markers**: delve, tapestry, testament, realm, pivotal, robust, seamless, leverage, utilize, empower, foster, streamline, unlock, harness, elevate, multifaceted, myriad, ever-evolving. | `ai-vocab` | `leverage` → `use`; `robust` → what it survives; `seamless` → what the user no longer has to do. One in a long piece is weak evidence; several are strong. |
| **Set phrases**: `it's worth noting`, `when it comes to`, `plays a crucial role`, `in today's fast-paced world`, `at its core`, `let's dive in`, `the X landscape`. | `stock-phrase` | Delete, then check whether the sentence still says anything. |
| **Big claims with nothing inside**: `The implications are significant.` | `vague-declarative` | Name the implication. |
| **Ceremony**: `marks a pivotal moment`, `stands as a testament`. | `stock-phrase`, `review` | `The release marks a pivotal moment for the team` → `It's the first release the team shipped without a freeze.` |
| **Tacked-on "-ing" commentary**: `…, highlighting the team's commitment to quality.` | `ing-rider` | Say what follows from it: `…, so a dropped connection no longer fails the job.` |
| **Brochure adjectives**: world-class, blazing fast, effortless. | `sales-language` | Give the measurement that would justify the adjective. |
| **Unnamed authorities**: `experts agree`, `studies show`. | `weasel` | Name and link the source, or drop the claim. Never invent one. |
| **"serves as", "stands as", "boasts"** where "is" or "has" would do. | `copula-avoidance` | `The service acts as a gateway` → `The service is a gateway`, or say what it does. |
| **Passive voice throughout.** | `passive` | Say who did it. In docs, passive is sometimes the clearest choice ("the file is created in…"). |
| **Sentences that would fit any article.** | `review` | Try moving the sentence into a piece about another product or company. If it still fits, it says nothing about this one; replace it with something only true here. |

## 4. Decorative formatting

| Habit | Rule | Notes |
|---|---|---|
| **Bold mid-sentence** for emphasis. | `bold-inline` | Let word order carry emphasis. In docs, bold for UI labels is fine. |
| **Lists of bold labels**: `- **Speed:** fast.` repeated. | `bold-label-list` | Write it as prose, or drop the labels. Fine in reference docs. |
| **Emoji in headings or as bullets.** | `emoji-heading`, `emoji-bullet` | Remove. Some LinkedIn readers tolerate an emoji or two in a post body, never as structure. |
| **A heading for every two sentences.** | `heading-density` | Merge the sections. |
| **Title Case Headings.** | `title-case-heading` | Use sentence case. |
| **Bullets for connected reasoning.** | `review` | If the items lean on each other ("because", "so"), it's a paragraph. |
| **Code blocks without a language.** | `code-fence-lang` | Add the language. |

## 5. Chat leftovers

| Habit | Rule |
|---|---|
| `Great question!`, `I hope this helps`, `Certainly!`, `Here is the revised version`, notes about a knowledge cutoff | `chat-residue` (always an error) |
| Cheerful sign-offs in docs: `Happy coding!`, `Congratulations!`, `Feel free to reach out` | `docs-cheer` and `stock-phrase` |
| Engagement bait: `Tag someone who…`, `Follow for more`, `Agree? ♻️` | `engagement-bait` |

## 6. Misjudged reader

Only a reader catches these. They are the main job of the `cold-reader` and `slop-hunter` reviewers.

- **Talking about the text instead of the subject.** `In this article we will explore…`, `As you can see…`, `This distinction matters.` Rule: `metadiscourse`. Delete the aside, or replace it with the fact it points at.
- **Explaining what the reader already knows.** Defining "API" for backend engineers. Decide who reads it and trust them.
- **Rotating synonyms.** "The agent… the assistant… the tool…" for one thing. Repeat the right word; in docs, one term per concept, always.
- **Placeholder scenarios.** "Imagine a company that wants to scale." Use the real case, or a precise made-up one that is labelled as made up.
- **Identical sections.** Every section the same length and shape, three bullets and a one-line summary each. Real material is uneven.
- **Hedging on every claim.** Each statement followed by "however, it depends". Take the position the author holds and state the condition that would change it.
- **Template titles.** `The Ultimate Guide to…`, `Unlocking…`, `X: A Deep Dive`, `Why X Matters More Than Ever`. See `articles.md`.
- **Lessons with no handle.** "The biggest lesson? Communication is key." State a lesson only if a reader could act on it and someone could disagree with it.

## Sources and credits

The catalogue and its examples are written for this skill. Its coverage owes a lot to these projects, all MIT-licensed unless noted:

- `blader/humanizer`: the pattern catalogue and its grouping, which this file reorganises;
- `hardikpandya/stop-slop`: rules and scoring;
- `petergyang/no-ai-slop`: the minimum-edit approach and the test of whether a sentence would fit another subject;
- `sergebulaev/linkedin-skills`: the density rule of one contrast and one triad per post;
- `eric-tramel/slop-guard`: a rule-based linter;
- Wikipedia's "Signs of AI writing" (CC BY-SA), paraphrased and not copied.
