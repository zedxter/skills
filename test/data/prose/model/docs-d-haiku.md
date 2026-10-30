# Contributing to <project-name>

Thanks for your interest in contributing. This guide covers how to report problems, propose changes, and get your code merged. Please read it before opening an issue or pull request.

By participating in this project, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Table of contents

- [Ways to contribute](#ways-to-contribute)
- [Before you start](#before-you-start)
- [Development setup](#development-setup)
- [Making changes](#making-changes)
- [Code style](#code-style)
- [Testing](#testing)
- [Commit messages](#commit-messages)
- [Submitting a pull request](#submitting-a-pull-request)
- [Review process](#review-process)
- [Reporting bugs and security issues](#reporting-bugs-and-security-issues)
- [License](#license)

## Ways to contribute

- **Report bugs** by opening an issue with a minimal reproduction.
- **Suggest features** by opening an issue that describes the problem you want solved, not only the solution.
- **Improve documentation**, including godoc comments, examples, and the README.
- **Fix issues** labeled `good first issue` or `help wanted`.
- **Review pull requests** from others. Careful reviews are as valuable as code.

## Before you start

- Search existing issues and pull requests to avoid duplicates.
- For anything larger than a small bug fix, open an issue or start a discussion first. This saves you from writing code that the maintainers cannot accept, for example because it conflicts with the project's design or direction.
- Changes that alter the public API, the on-disk or wire format, or supported Go versions need a design discussion before implementation.

## Development setup

### Requirements

- Go <min-version> or later (see `go.mod` for the minimum supported version)
- `git`
- `golangci-lint` <version> (optional locally, required in CI)

### Getting the code

```sh
git clone https://github.com/<org>/<project>.git
cd <project>
go mod download
```

### Verifying your setup

```sh
go build ./...
go test ./...
```

If both commands succeed, you are ready to work.

## Making changes

1. **Fork** the repository and clone your fork.
2. **Create a branch** from `main` with a descriptive name:
   ```sh
   git checkout -b fix/parser-nil-panic
   ```
3. **Make focused changes.** One pull request should address one problem. Unrelated refactors belong in separate pull requests.
4. **Keep the public API stable.** If you must change an exported identifier, explain why in the pull request and note it in the changelog.
5. **Update documentation** for any exported symbol or user-visible behavior you change.
6. **Add or update tests** for every behavior change (see [Testing](#testing)).

## Code style

We follow the standard Go conventions plus the guidance below.

- Run `gofmt` (or `goimports`) on all changed files. CI rejects unformatted code.
- Run `go vet ./...` and `golangci-lint run`. Fix or justify every finding. Use `//nolint` only with a specific linter name and a reason, for example `//nolint:errcheck // Close error is irrelevant on read-only file`.
- Follow [Effective Go](https://go.dev/doc/effective_go) and the [Go Code Review Comments](https://go.dev/wiki/CodeReviewComments).
- Every exported identifier needs a doc comment that begins with its name.
- Return errors instead of panicking in library code. Wrap errors with context using `fmt.Errorf("doing X: %w", err)`.
- Accept `context.Context` as the first parameter for functions that do I/O or may block. Do not store contexts in structs.
- Prefer small interfaces defined by the consumer, not the implementer.
- Avoid global mutable state. If you must use it, document how it is synchronized.
- Keep dependencies minimal. Adding a new third-party module requires justification in the pull request.
- Match the naming, comment density, and idioms of the code around your change.

## Testing

- All new behavior needs tests. All bug fixes need a regression test that fails without the fix.
- Use table-driven tests where several inputs share one assertion shape.
- Run the full suite with the race detector before submitting:
  ```sh
  go test -race -count=1 ./...
  ```
- Tests must be deterministic. Do not depend on wall-clock time, network access, or test ordering. Inject clocks and fakes instead.
- Mark helpers with `t.Helper()` and use `t.Cleanup` for teardown.
- Integration tests that need external services belong behind a build tag (for example `//go:build integration`) and must be documented in the README.
- Benchmarks are welcome for performance-sensitive changes. Include before-and-after results in the pull request.

## Commit messages

We use the following format, loosely based on [Conventional Commits](https://www.conventionalcommits.org/):

```
<area>: <short summary in the imperative mood>

<body explaining what changed and why, wrapped at 72 columns>

Fixes #<issue-number>
```

- The subject line is 72 characters or fewer.
- Use the affected package path as the area, for example `parser:`, `client/http:`, or `docs:`.
- Explain *why* in the body. The diff already shows *what*.
- Reference related issues with `Fixes #123` or `Refs #123`.

Examples:

```
parser: return error instead of panicking on nil input

Previously Parse dereferenced a nil *Reader, which crashed callers
that passed an uninitialized reader. It now returns ErrNilReader.

Fixes #482
```

## Submitting a pull request

Before opening a pull request, confirm that:

- [ ] `go test -race ./...` passes locally
- [ ] `golangci-lint run` reports no new issues
- [ ] `gofmt` has been run on changed files
- [ ] New and changed behavior is covered by tests
- [ ] Exported identifiers have doc comments
- [ ] `CHANGELOG.md` has an entry under "Unreleased", if the change is user-visible
- [ ] Commits follow the message format above

In the pull request description, include:

- **What** the change does, in a sentence or two
- **Why** it is needed, and a link to the related issue
- **How** it was tested
- **Breaking changes**, if any, and the migration path

Mark the pull request as a **draft** if it is not ready for review. Pull requests that stay inactive for 30 days may be closed. You are welcome to reopen them later.

## Review process

- A maintainer will triage new pull requests within about one week. If you have not heard back in that time, leave a polite comment on the pull request.
- At least one maintainer approval is required to merge. Changes to public APIs or core packages may require two.
- Reviews focus on correctness, API design, readability, and test coverage. Expect requests for changes. They are a normal part of the process and not a judgment of you.
- Address feedback with new commits so reviewers can see what changed. Maintainers will squash on merge unless asked otherwise.
- Once approved, a maintainer will merge the pull request. Please do not merge your own pull request unless a maintainer has said you may.

## Reporting bugs and security issues

**Bugs:** Open an issue and include:

- The version of the project and your Go version (`go version`)
- Your operating system and architecture
- Minimal code or steps that reproduce the problem
- What you expected to happen and what actually happened, including any panic output or error messages

**Security vulnerabilities:** Do not open a public issue. Report them privately through [GitHub Security Advisories](https://github.com/<org>/<project>/security/advisories/new) or by email to <security-contact>. We aim to acknowledge reports within 3 business days and to publish a fix and advisory as quickly as the severity warrants.

## License

By contributing, you agree that your contributions will be licensed under the same license as the project, <license-name>. Make sure you have the right to submit the code you are contributing. <!-- If the project requires a Developer Certificate of Origin, add: sign off each commit with `git commit -s`. -->
