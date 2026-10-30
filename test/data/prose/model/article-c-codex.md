# How to Run Code Reviews That Actually Improve Your Code

A pull request sits untouched for two days. When someone finally reviews it, most of the comments concern naming and formatting. The author makes the changes, gets an approval, and merges.

A week later, the team discovers that the change lets users access records they don’t own.

Everyone followed the review process. The process missed what mattered.

Effective code reviews need a clear purpose: find consequential problems, make the code easier to maintain, and share enough understanding that the next person can change it safely. Achieving that takes deliberate work from both authors and reviewers.

## Start with a change someone can understand

Review quality starts before a reviewer opens the diff.

A pull request that mixes a feature, a dependency upgrade, and a large refactor forces the reviewer to reconstruct several stories at once. That makes it harder to distinguish an intentional behavior change from an accidental one.

Keep each pull request focused on one coherent outcome. Separate mechanical changes from behavioral changes when doing so makes the work easier to assess. Avoid splitting changes so aggressively that every piece requires reading three other pull requests to understand it.

A useful test is: **Can the reviewer explain what this change does in one sentence?**

If the answer requires several unrelated clauses, the scope probably needs attention.

Before requesting review, read your own diff. Remove debugging code, check for accidental files, and run the relevant checks. Your reviewer’s attention is limited; spend it on questions that require judgment.

## Give the reviewer a map

A diff shows what changed. It rarely explains why.

A useful pull request description gives the reviewer enough context to evaluate the implementation:

- What problem does this solve?
- What behavior changes?
- Why was this approach chosen?
- How was it verified?
- Where does it need particular attention?

Be concrete. “Fix permissions” gives the reviewer little to work with. “Prevent workspace members from downloading invoices belonging to another workspace” identifies both the intended behavior and the boundary worth checking.

Verification should be equally specific. “Tests pass” says less than “Added coverage for cross-workspace requests and confirmed that existing invoice downloads still succeed.”

For a complex change, suggest a reading order. A sentence such as “Start with the authorization helper, then check its two callers” can save considerable effort.

## Review the behavior before the details

It is tempting to start commenting as soon as you see an awkward variable name. Resist that impulse long enough to understand the change.

First, read the description and identify the intended outcome. Then follow the main execution path. Check surrounding code, callers, and assumptions where necessary; the changed lines alone may not reveal the problem.

Review in roughly this order:

1. **Correctness:** Does the implementation produce the intended behavior?
2. **Failure paths and boundaries:** What happens with invalid input, missing permissions, partial failures, retries, or unexpected state?
3. **Design and maintainability:** Does the approach fit the system, and can someone safely modify it later?
4. **Verification:** Do the tests exercise the behavior and meaningful failure cases?
5. **Local clarity:** Are names, comments, and structure understandable?

Adjust the emphasis to the change. A database migration deserves attention to compatibility, locking, and recovery. A payment retry deserves scrutiny for duplicate effects. A copy change rarely needs that depth.

**Spend review effort in proportion to the consequences of being wrong.**

## Make comments actionable

A review comment should help the author make a decision.

“This is confusing” identifies a reaction but leaves the author to diagnose it. A stronger comment points to the issue and explains its effect:

> “This function returns `null` for both ‘record missing’ and ‘permission denied.’ The caller treats both as a 404. Is that intentional? If those cases need different handling, we should preserve the distinction here.”

Good comments usually contain an observation, a consequence, and a question or proposed next step. They don’t need to be long.

For a clear defect, be direct:

> “This retries after a timeout, but the first request may already have created the charge. We need an idempotency mechanism before retrying.”

For an optional improvement, say so:

> “Suggestion: naming this `eligibleAccounts` would make the filtering rule easier to follow.”

Distinguish required changes from suggestions and minor preferences. Your team can use labels such as “blocking,” “suggestion,” and “nit,” or simply make the distinction in ordinary language. The important part is that the author knows what must change before merge.

Avoid disguising instructions as questions. “Could we perhaps add authorization?” sounds optional when authorization is required. Explain the requirement plainly.

## Keep taste from becoming policy

Some comments identify defects. Others express a preference between reasonable alternatives.

Before asking for a change, consider:

- Does this violate an established convention?
- Does it create a concrete maintenance problem?
- Can I explain the benefit beyond “I prefer it”?

When two approaches are both clear and correct, accepting the author’s choice is often the productive decision.

Recurring conventions belong in shared guidance, formatters, linters, or other automated checks. Repeatedly negotiating import order in pull requests consumes attention without improving the team’s judgment.

Automation should handle predictable checks so people can focus on behavior, assumptions, and trade-offs.

## Treat tests as evidence

Passing tests are useful evidence, but they do not settle whether a change is correct.

Read the tests with the same care as the implementation. Ask whether they would catch the failure the change is supposed to prevent. Look for assertions about observable behavior, rather than assertions that merely repeat the code’s internal steps.

For a bug fix, a valuable question is:

> Would this test fail against the previous implementation?

For new behavior, inspect the important boundaries. A successful request may be covered while unauthorized access, empty input, or a repeated request remains untested.

Request tests where they reduce meaningful uncertainty. An extra test that mirrors a trivial implementation may add maintenance cost without making the change safer.

## Keep the review moving

Slow reviews make small changes expensive. Authors lose context, work piles up, and people start combining changes to avoid another round of waiting.

Set a realistic team expectation for initial review. If you cannot review within that window, say so and help route the work to someone who can.

When reviewing, aim to provide a coherent round of feedback. Finish understanding the change before sending a stream of comments that may contradict one another. After revisions, check the affected areas and unresolved issues without restarting settled discussions unnecessarily.

If a thread keeps growing without convergence, talk directly. A short conversation can expose a missing assumption much faster than another page of comments. Record the resulting decision in the pull request so others can understand it later.

## Make disagreement useful

Authors should explain their reasoning, ask for clarification when needed, and challenge suggestions that would weaken the change. Reviewers should be willing to revise their feedback when new context appears.

Keep the discussion attached to the code and its consequences.

“This approach makes it possible to skip validation” gives the team something to investigate. “You always overcomplicate things” makes the conversation personal and harder to resolve.

When a disagreement persists, identify what kind it is. A correctness question may be settled by a failing example. A design trade-off may need a small experiment or a decision from the responsible maintainer. A preference dispute may need someone to accept a reasonable alternative.

Define who makes the final call. Consensus is useful, but indefinite discussion is costly.

## Approve with a clear standard

Approval should mean that you understand the change well enough to support merging it, within the scope of your review.

That does not require certainty about every possible outcome. It does require resolving material concerns and being explicit about limits. If you reviewed the API behavior but lack the expertise to assess the cryptography, say so and involve an appropriate reviewer.

Before approving, ask:

- Does the change solve the stated problem?
- Have the consequential risks been addressed?
- Is the verification appropriate?
- Can the next maintainer understand the implementation?

A pull request can meet that standard while still leaving room for future improvement. Keep unrelated cleanup separate unless it is necessary to make the current change safe or understandable.

The best review leaves the code in better shape and gives the team more shared understanding. It also lets the work move forward. A clear description, a focused diff, and a handful of consequential comments will usually do more for that goal than a long debate over every line.
