# AI tells

Patterns that make prose read as produced by a chat model. Each entry has the linter rule id where a regex can catch it, or `review` where only a reader can. Each entry also says where the pattern is legitimate, because every one of these devices appears in good human writing. **The tell is density plus emptiness:** the device used by habit, where it carries no information.

Severity in the linter differs by genre (`lint_prose.py --list-rules`). In docs, repeated structure, headings and lists are the job, so most rhythm rules are off there.

How to fix any of them: find the point the sentence was circling, then state it with the specific fact, name or number that proves it. Replacing a flagged word with its synonym keeps the shape and the tell.

## A. Staging instead of stating

The sentence performs a reveal instead of saying the thing.

| Pattern | Rule | Before → after |
|---|---|---|
| **Not X, but Y.** A contrast with a claim nobody made. | `not-x-but-y` | `It's not about the model. It's about the eval.` → `The eval matters more than the model.` |
| **Negative listing.** | `negative-list` | `Not a framework. Not a library. A set of scripts.` → `It's a set of scripts.` |
| **Colon reveal.** A short setup, a colon, a lowercase punchline. | `colon-reveal` | `The best part: it learns.` → `It learns from each run, which is why we kept it.` |
| **Self-answered question.** | `rhetorical` | `The result? Faster builds.` → `Builds got faster: 14 minutes down to 6.` |
| **Throat-clearing opener.** | `throat-clearing` | `Here's the thing: caching hides bugs.` → `Caching hides bugs.` |
| **Faux-insight setup.** Flatters the writer as the one who knows. | `faux-insight` | `What most people get wrong is the index.` → `We built the wrong index.` |
| **One-line kicker** after every paragraph, often an aphorism. | `kicker` | Paragraph … `And that changes everything.` → end on the last concrete sentence. |
| **Recap ending.** The reader was just there. | `recap-ending` | `In conclusion, observability matters.` → end on the next action or the last result. |
| **Arguing with no one.** "Some say X" where nobody does. | `review` | Cut the strawman; argue with a named position or none. |

Legitimate: one sharp contrast where the reader really does believe X; a question you leave open for the reader at the end of a post.

## B. Rhythm by rule

| Pattern | Rule | Notes |
|---|---|---|
| **Forced triads.** Three adjectives, three benefits, three clauses, by habit. | `triad` | Budget: about one per 250 words. Two items or four break the pattern; often one is the real point. |
| **Fragment stacks.** `Fast. Cheap. Reliable.` | `fragment-stack` | Write the sentence that says how and why. |
| **Repeated openers.** Three sentences in a row starting with the same word. | `repeated-opener` | Anaphora on purpose is fine once. |
| **Metronomic length.** Every sentence 15–22 words. | `uniform-rhythm` | Human prose varies. Don't fake it with random short sentences; let a long explanation run and a plain fact stand short. |
| **Dashes as the universal connector.** | `em-dash` | Budget scales with length. In posts, prefer none. Use periods, commas, colons, parentheses. The author's documented habit overrides. |
| **Stacked hedges.** `could potentially`, three qualifiers in one sentence. | `hedge-stack` | Keep the one hedge that is true. |
| **Intensifiers.** very, truly, literally, fundamentally. | `intensifier` | Let the fact carry the weight. |

## C. Inflation and borrowed authority

| Pattern | Rule | Before → after |
|---|---|---|
| **AI-marker vocabulary**: delve, tapestry, testament, realm, pivotal, robust, seamless, leverage, utilize, empower, foster, streamline, unlock, harness, elevate, multifaceted, myriad, ever-evolving. | `ai-vocab` | `leverage` → `use`; `robust` → say what it survives; `seamless` → say what the user no longer has to do. One in a long piece is weak evidence; a cluster is strong. |
| **Stock phrases**: `it's worth noting`, `when it comes to`, `plays a crucial role`, `in today's fast-paced world`, `at its core`, `let's dive in`, `the X landscape`. | `stock-phrase` | Cut, then check whether the sentence still says anything. |
| **Vague declaratives**: `The implications are significant.` | `vague-declarative` | Name the implication. |
| **Inflated significance**: `marks a pivotal moment`, `stands as a testament`. | `stock-phrase`, `review` | `The launch marks a pivotal moment` → `It is the company's first paid product.` |
| **Shallow -ing riders**: `…, highlighting the team's commitment to quality.` | `ing-rider` | Say what actually follows: `…, so a dropped connection no longer fails the job.` |
| **Sales adjectives**: world-class, blazing fast, effortless, best-in-class. | `sales-language` | Give the number that earns the adjective. |
| **Weasel attribution**: `experts agree`, `studies show`. | `weasel` | Name and link the source, or drop the claim. Never invent the source. |
| **Avoiding is/has**: `serves as`, `stands as`, `boasts`. | `copula-avoidance` | `The app serves as a hub` → `The app is a hub`, or better, say what it does. |
| **Passive voice everywhere.** | `passive` | Name who did it. In docs, passive is sometimes right ("the file is created in…"). |
| **The portability test** | `review` | If a sentence could move unchanged into an article about another company, product or person, it is filler. Replace it with something only true here. |

## D. Formatting by rule

| Pattern | Rule | Notes |
|---|---|---|
| **Bold as decoration** inside sentences. | `bold-inline` | Emphasis comes from sentence structure. In docs, bold for UI labels is fine. |
| **Bold-label lists**: `- **Speed:** fast.` repeated. | `bold-label-list` | Write the list as prose, or drop the labels. Fine in reference docs. |
| **Emoji headings and emoji bullets.** | `emoji-heading`, `emoji-bullet` | Remove. Some LinkedIn audiences tolerate an emoji or two in a post body; never as structure. |
| **Headings over two-sentence sections.** | `heading-density` | Merge sections. An article is not a slide deck. |
| **Title Case Headings.** | `title-case-heading` | Sentence case. |
| **Bullets where prose reads better.** | `review` | If items depend on each other ("because", "so"), it's a paragraph. |
| **Unlabelled code blocks.** | `code-fence-lang` | Add the language. |

## E. Leftovers from the chat

| Pattern | Rule |
|---|---|
| `Great question!`, `I hope this helps`, `Certainly!`, `Here is the revised version`, knowledge-cutoff notes | `chat-residue` (always an error) |
| `Happy coding!`, `Feel free to reach out` as a docs sign-off | `stock-phrase`, `review` |
| Engagement bait: `Tag someone who…`, `Follow for more`, `Agree? ♻️` | `engagement-bait` |

## F. Writing for the wrong reader

Only a reader catches these; they are the main job of the `cold-reader` and `slop-hunter` reviewers.

- **Writing about the text instead of the subject.** `In this article we will explore…`, `As you can see…`, `This distinction matters.` Rule: `metadiscourse`. Delete, or replace with the fact the aside points at.
- **Re-explaining what the reader knows.** Defining "API" for backend engineers. Decide who the reader is and trust them.
- **Synonym cycling.** "The agent… the assistant… the tool…" for one thing. Repeat the right word; in docs, one term per concept, always.
- **Generic examples.** "Imagine a company that wants to scale." Use the real case or a precise invented one that is labelled as such.
- **Symmetric structure.** Every section the same length and shape, each with three bullets and a one-line summary. Real material is lumpy.
- **Both-sides hedging.** Every claim followed by "however, it depends". Take the position the author actually holds, with the condition that would change it.
- **Formula titles.** `The Ultimate Guide to…`, `Unlocking…`, `X: A Deep Dive`, `Why X Matters More Than Ever`. See `articles.md` for titles.
- **The empty lesson.** "The biggest lesson? Communication is key." A lesson is only worth stating if the reader could act on it, and if it's specific enough that someone could disagree.

## Sources

The catalogue is our own wording. It is informed by:

- `blader/humanizer` (MIT);
- `hardikpandya/stop-slop` (MIT);
- `petergyang/no-ai-slop` (MIT);
- `sergebulaev/linkedin-skills` (the density rule);
- `eric-tramel/slop-guard` (MIT);
- Wikipedia's "Signs of AI writing" (CC BY-SA, paraphrased and not copied).
