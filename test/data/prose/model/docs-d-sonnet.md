# Contributing to This Project

Thank you for your interest in contributing! This guide explains how to report issues, propose changes, and get your code merged.

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Table of Contents

- [Ways to Contribute](#ways-to-contribute)
- [Getting Started](#getting-started)
- [Development Workflow](#development-workflow)
- [Coding Guidelines](#coding-guidelines)
- [Testing](#testing)
- [Commit Messages](#commit-messages)
- [Pull Requests](#pull-requests)
- [Reporting Bugs](#reporting-bugs)
- [Requesting Features](#requesting-features)
- [Security Issues](#security-issues)
- [Release Process](#release-process)
- [License](#license)

## Ways to Contribute

You don't have to write code to help:

- Report bugs and suggest improvements
- Improve documentation, examples, and godoc comments
- Triage issues and reproduce reported problems
- Review pull requests
- Answer questions in Discussions

Issues labeled `good first issue` and `help wanted` are good places to start.

## Getting Started

### Prerequisites

- [Go](https://go.dev/dl/) (the minimum version is listed in `go.mod`; we test against the two latest stable releases)
- `git`
- `make`
- [golangci-lint](https://golangci-lint.run/welcome/install/)

### Set Up Your Environment

1. Fork the repository on GitHub.
2. Clone your fork:

   ```sh
   git clone https://github.com/<your-username>/<project>.git
   cd <project>
   ```

3. Add the upstream remote:

   ```sh
   git remote add upstream https://github.com/<org>/<project>.git
   ```

4. Verify everything builds and passes:

   ```sh
   make build
   make test
   ```

## Development Workflow

1. Sync with upstream and create a branch:

   ```sh
   git fetch upstream
   git checkout -b my-change upstream/main
   ```

2. Make your changes in small, focused commits.
3. Run the checks locally:

   ```sh
   make fmt     # gofmt / goimports
   make lint    # golangci-lint
   make test    # unit tests with the race detector
   ```

4. Push to your fork and open a pull request against `main`.

For large changes (new features, API changes, significant refactors), please open an issue first to discuss the approach. This avoids wasted effort if the change doesn't fit the project's direction.

## Coding Guidelines

We follow standard Go conventions:

- [Effective Go](https://go.dev/doc/effective_go)
- [Go Code Review Comments](https://go.dev/wiki/CodeReviewComments)
- [Google Go Style Guide](https://google.github.io/styleguide/go/)

Specifics:

- **Formatting:** Code must be formatted with `gofmt` and `goimports`. CI will fail otherwise.
- **Linting:** Code must pass `golangci-lint run` using the repository's `.golangci.yml`.
- **Naming:** Use short, clear names. Avoid stuttering (`foo.FooClient` → `foo.Client`).
- **Errors:** Return errors rather than panicking. Wrap with context using `fmt.Errorf("doing x: %w", err)`. Error strings are lowercase and have no trailing punctuation.
- **Context:** Functions that perform I/O or may block should accept `context.Context` as the first parameter.
- **Concurrency:** Document goroutine ownership and lifecycle. Avoid leaking goroutines; every goroutine should have a clear way to stop.
- **Dependencies:** Prefer the standard library. New third-party dependencies need justification in the PR description. Run `go mod tidy` before committing.
- **Public API:** Every exported identifier needs a godoc comment beginning with its name. Avoid breaking changes to exported APIs; if unavoidable, call them out clearly in the PR.
- **Comments:** Explain *why*, not *what*.

## Testing

All changes should include tests.

- Place tests in `_test.go` files next to the code they cover.
- Prefer table-driven tests with subtests (`t.Run`).
- Use `t.Helper()` in test helpers and `t.Cleanup()` for teardown.
- Avoid sleeping in tests; use channels, contexts, or fake clocks.
- Run with the race detector:

  ```sh
  go test -race ./...
  ```

- Check coverage for your changes:

  ```sh
  go test -cover ./...
  ```

- Add benchmarks for performance-sensitive code and include before/after results in the PR (`benchstat` is helpful).
- Bug fixes should include a regression test that fails without the fix.

Integration tests live under `./integration` and may require extra setup; see the README in that directory.

## Commit Messages

Write clear, descriptive commit messages:

```
pkg: short summary in imperative mood (50 chars or less)

Longer explanation of what changed and why, wrapped at 72 columns.
Reference related issues below.

Fixes #123
```

- Prefix the summary with the affected package (e.g., `parser: handle empty input`).
- Use the imperative mood ("add", not "added").
- Reference issues with `Fixes #N` or `Updates #N`.

We may squash commits on merge, so the pull request title should follow the same format.

## Pull Requests

Before submitting, make sure:

- [ ] Tests pass locally (`make test`)
- [ ] Lint and formatting pass (`make lint fmt`)
- [ ] New code is covered by tests
- [ ] Documentation and godoc are updated
- [ ] `CHANGELOG.md` is updated for user-facing changes
- [ ] The PR is focused on a single concern

In the PR description, explain:

- **What** the change does
- **Why** it is needed
- **How** it was tested
- Any breaking changes or follow-up work

### Review Process

- A maintainer will review your PR, usually within a few business days. Feel free to ping politely if it has been longer.
- Address feedback by pushing new commits; avoid force-pushing during review so reviewers can follow changes.
- CI must pass before merging.
- At least one maintainer approval is required.
- Maintainers may request changes, suggest alternatives, or decline changes that don't fit the project's scope.

## Reporting Bugs

Search [existing issues](../../issues) first. If none match, open a new issue including:

- Go version (`go version`) and OS/architecture
- Project version or commit hash
- Minimal code or steps to reproduce
- Expected behavior and actual behavior
- Relevant logs, stack traces, or error messages

A small, self-contained reproduction (ideally a failing test or a [Go Playground](https://go.dev/play/) link) is the fastest way to get a bug fixed.

## Requesting Features

Open an issue describing:

- The problem you're trying to solve (not just the solution you want)
- Your proposed approach and alternatives considered
- Whether you're willing to implement it

## Security Issues

**Do not report security vulnerabilities in public issues.** Follow the instructions in [SECURITY.md](SECURITY.md) to report them privately. We will acknowledge your report promptly and work with you on a fix and coordinated disclosure.

## Release Process

Maintainers handle releases. The project follows [Semantic Versioning](https://semver.org/):

1. Update `CHANGELOG.md`.
2. Tag the release (`vX.Y.Z`) on `main`.
3. CI publishes release artifacts and the Go module proxy picks up the new tag.

Breaking changes are only made in major versions (or following `v0` conventions, in minor versions with clear notes).

## License

By contributing, you agree that your contributions will be licensed under the same license as the project (see [LICENSE](LICENSE)). If the project requires a Developer Certificate of Origin, sign off your commits with `git commit -s`.

---

Thanks again for helping make this project better!
