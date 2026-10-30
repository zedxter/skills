# How to Run a Code Review That Improves the Code and the Team

The first code review I remember was a disaster, and I was the one who caused it. A colleague had sent a patch that rewrote a date-parsing function. I left fourteen comments. Eleven were about naming and brace placement. The remaining three were about a bug that would have corrupted timestamps for anyone east of UTC+8. My colleague fixed all fourteen and was clearly worn out by the end. The timestamp bug shipped anyway, because one of my three real comments was phrased as a question and got answered with "yes, I think that's fine."

That episode taught me what a review is for. It is a conversation about whether a change is correct and whether the team can live with it. Everything else, from tooling to etiquette, serves that conversation.

## Decide what you are looking for before you open the diff

Reviewers who open a diff with no plan tend to comment on whatever is most visible. Style issues are visible. Wrong assumptions are not. So I start by reading the description and asking what this change is supposed to do. If the description doesn't say, that's the first comment, and I stop there until it's answered. Reviewing code without knowing its purpose means guessing, and the author deserves better than a guess.

Then I read the tests before the implementation. Tests tell you what the author believes the code does. If the tests cover only the happy path, I already know where to look in the implementation. If there are no tests, I ask what would convince the author that the change works, since that is usually a more useful question than "please add tests."

Only after that do I read the production code, and I read it in the order that makes sense for the logic, not the alphabetical order the review tool offers. Most tools sort files by path, which has nothing to do with how the change hangs together.

## Keep the change small enough to review

Review quality falls off quickly with size. A forty-line change gets read. A twelve-hundred-line change gets skimmed, and the reviewer approves it with a vague sense that nothing looked obviously wrong. Studies of review effectiveness keep landing in the same place: defect detection drops once a single review passes a few hundred lines.

This is mostly the author's responsibility, and it is worth saying out loud on a team. Split refactors from behavior changes. Put renames in their own commit. If a feature needs a new table, a service, and a UI, ship them in that order, each behind a flag if necessary. A reviewer can hold one idea in their head at a time, and a pull request that mixes three of them guarantees that at least two get less attention than they need.

When you receive a change that is too large, say so before reading it. "This is too big for me to review well, can we split it?" is a legitimate response, and it respects the author's time more than a rubber stamp does.

## Separate what must change from what you would have done differently

Nearly every review contains two kinds of comments, and authors can't tell them apart unless you mark them. One kind says the code is wrong or will cause harm. The other says you would have written it another way. Mixing them forces the author to guess which ones block the merge, and careful authors will treat all of them as blocking.

I prefix comments when the distinction matters. "Blocking:" for anything I would not merge without. "Nit:" for taste. "Question:" when I'm asking because I don't understand, and I'm prepared to be told I'm wrong. This takes two seconds per comment and removes a whole category of back-and-forth.

I also try to delete my own nits before submitting. Reading back through a draft review, I often find several comments that exist only because I noticed something. If a linter could catch it, the linter should. If it doesn't affect correctness or readability for the next person, it probably doesn't need saying. A review with four comments that matter is better than one with thirty that bury them.

## Say why, and offer something

"This is confusing" is a report about the reviewer. "This function returns null on failure but throws on bad input, so callers have to handle both, and the two call sites in billing only handle one" is information the author can act on. Whenever I flag a problem, I try to include the reason and, where I can, a concrete alternative.

The alternative does not have to be right. Often the author knows the surrounding code better than I do and will pick a different fix. But a suggestion shows that the problem is solvable and gives the author a starting point, which is far better than a comment that leaves them to work out what you wanted.

Questions deserve the same care. A question like "why not use a map here?" can be a real question or a polite way of saying "use a map." Authors read it as the second, so if you mean the second, say it directly. If you mean the first, say what you're uncertain about so they can answer the actual concern.

## Review the code, not the person

Comments that begin with "you" tend to go badly. "You forgot to handle the empty list" is the same observation as "this doesn't handle the empty list," but the first one is addressed to a person and the second to a piece of code. The difference is small on the page and large in how it lands, especially with junior engineers who are already wondering how they're perceived.

Tone also travels badly through text. A comment that sounds neutral to you may sound curt to someone who has been working on the change for three days. I add a sentence of context when a comment could be misread, and I say what I liked when something was done well. That last part is not flattery. Authors learn what to repeat from praise just as they learn what to avoid from criticism, and a review that only lists problems gives them half the signal.

When a disagreement runs past two rounds of comments, move it out of the thread. Five minutes on a call settles what fifteen replies won't, and you can write the outcome back into the review so it stays on record.

## Be fast, within reason

A review that sits for three days costs more than the time it takes to write. The author loses context, starts something else, and then has to reload the original problem when comments finally arrive. Many teams set an informal target of one business day for a first response, and that seems about right to me.

Speed does not mean skimming. It means setting aside time on a schedule instead of waiting for inspiration. I review first thing in the morning and again after lunch, before I start my own deep work. Reviews that are squeezed in between meetings are the ones where I catch the least.

If you cannot give a change proper attention today, say so and name someone who can. An honest "I can't get to this until Thursday" beats a quick approval you will regret.

## Know what a review cannot do

A review will not find every bug. Human readers are bad at spotting problems in code that looks reasonable, and a date-parsing bug that appears only in certain time zones looks reasonable to nearly everyone. Tests, type systems, staging environments, and monitoring all catch things that reading cannot. The review's particular strength is catching design problems and misunderstandings early, and spreading knowledge about how the system works so that more than one person can fix it at three in the morning.

That last benefit is easy to undervalue. When a reviewer reads a change in a part of the codebase they rarely touch, they learn something, and the author gets a second person who can answer questions about it later. Teams that rotate reviewers on purpose, instead of always sending changes to the same expert, end up with fewer single points of failure.

## Looking back at that first review

If I could redo that date-parsing review, I would leave three comments instead of fourteen. The first would say that the offset calculation looks wrong for positive offsets and give a failing example. The second would ask for a test with a non-UTC timezone. The third would point out that the helper duplicates one already in the utilities module. I'd phrase the first as a plain statement, not a question, and I'd mark it as blocking.

The brace placement could have gone to a formatter. The naming comments could have waited for a follow-up, or been dropped. My colleague would have spent an hour on the fixes instead of a day, and the timestamps would have been correct.
