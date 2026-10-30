# Contributing

Thanks for your interest in the project. This document explains how to get a change from your machine into the main branch with as little back and forth as possible.

## Before you start

If you found a bug, search the existing issues first. Someone may have reported it already, and a comment with your own reproduction is often more useful than a new issue.

If you want to add a feature or change behavior, open an issue before writing code. Describe the problem you are trying to solve, not only the solution you have in mind. Maintainers can often point to an existing way to do it, or to a reason an approach was rejected earlier. A short discussion costs far less than a pull request that cannot be merged.

Typo fixes, documentation corrections, and small bug fixes with an obvious cause can go straight to a pull request.

## Setting up

You need Go 1.22 or newer. Check with `go version`.

Fork the repository, then clone your fork:

```
git clone https://github.com/<your-username>/<project>.git
cd <project>
git remote add upstream https://github.com/<org>/<project>.git
```

Build and run the tests to confirm your environment works:

```
go build ./...
go test ./...
```

If a test fails on a clean checkout, that is worth an issue. Include your OS, architecture, and Go version.

Some tests talk to external services and are skipped by default. Set `INTEGRATION=1` to run them. You do not need to do this for most changes, and CI runs them for you.

## Making a change

Create a branch from the latest main. Name it for what it does, such as `fix-parser-panic` or `add-timeout-option`.

Keep each pull request to one logical change. If you notice an unrelated problem while working, put it in a separate branch. Reviewers read small diffs carefully and large ones quickly, and you want the careful reading.

### Code style

Run `gofmt` and `go vet` before committing. CI rejects code that fails either. We also run `staticcheck`, and you can install it with `go install honnef.co/go/tools/cmd/staticcheck@latest`.

Beyond that, follow the conventions in *Effective Go* and the Go Code Review Comments wiki page. Where the existing code in a package does something differently from those guides, match the package.

A few project habits worth knowing:

- Errors are wrapped with `fmt.Errorf("doing x: %w", err)`. The message says what the code was trying to do, not that it failed.
- Exported identifiers need doc comments that start with the identifier's name.
- We avoid adding dependencies. If you think one is justified, explain why in the pull request. The standard library covers more than people expect.
- Public API changes need extra care. Anything exported is something users may depend on, so removing or changing it requires a deprecation period and a note in the changelog.

### Tests

Every bug fix should come with a test that fails without the fix. Every new feature needs tests for the normal path and for the failure cases you can think of.

Prefer table-driven tests where several inputs share the same logic. Use `t.Helper()` in test helpers and `t.TempDir()` instead of creating files by hand. Avoid tests that depend on timing or on the order of map iteration, since they fail intermittently and waste everyone's time later.

If you change performance-sensitive code, add or update a benchmark and include before and after numbers from `go test -bench` in the pull request description.

### Documentation

If your change alters behavior that users can see, update the README, the package docs, or the files under `docs/` in the same pull request. Add an entry under "Unreleased" in `CHANGELOG.md`, written for someone upgrading who has not read the code.

## Commits

Write commit messages in the imperative mood: "Fix panic on empty input", not "Fixed" or "Fixes". Keep the first line under about 70 characters. If the reason for the change is not obvious from the diff, explain it in the body. The reason is the part nobody can reconstruct later.

Reference the related issue with `Fixes #123` where one exists.

You do not need to squash your commits before opening the pull request. Maintainers squash on merge. You may find it easier to push follow-up commits during review than to rewrite history, since that lets reviewers see what changed between rounds.

## Opening a pull request

Push your branch to your fork and open a pull request against `main`. In the description, say what the change does and why. Link the issue if there is one. If you made a choice that a reviewer might question, mention it up front.

Before you submit, check that:

- `go test ./...` passes locally
- `gofmt`, `go vet`, and `staticcheck` report nothing
- new behavior has tests and, where users will notice it, documentation

CI runs on every push. If it fails and you cannot tell why, ask in a comment. Flaky failures happen, and a maintainer can re-run the job.

## Review

A maintainer will usually respond within a week. If you have heard nothing after ten days, a polite ping on the pull request is fine.

Review comments are about the code, and they are meant to improve the change. Expect questions and requests for changes, including on contributions from experienced people. If you disagree with a suggestion, say so and give your reasoning. You may be right, and the discussion usually improves the result either way.

Push your fixes as new commits and reply to each comment so the reviewer knows it was handled. When everything is resolved, a maintainer will merge.

Sometimes a change is declined even though the work is good, because it does not fit the direction of the project or would be hard to maintain. This is a judgment about scope, and it is better to hear it early, which is another reason to open an issue first.

## Reporting security problems

Do not open a public issue for a vulnerability. Email `security@example.org` with a description and, if possible, a way to reproduce it. We will acknowledge the report within three days and keep you informed as we work on a fix. Please give us a reasonable amount of time to release it before you disclose the details.

## Licensing

By submitting a contribution, you agree that it is licensed under the same terms as the project, as described in the `LICENSE` file. We use the Developer Certificate of Origin rather than a contributor agreement: add a `Signed-off-by` line to your commits with `git commit -s` to certify that you have the right to submit the work.

## Questions

If something here is unclear or out of date, open an issue or a pull request. This document gets the same treatment as the code.
