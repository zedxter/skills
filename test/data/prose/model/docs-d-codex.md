# Contributing

Thanks for your interest in contributing! Bug reports, documentation improvements, tests, and code changes are welcome.

## Getting started

1. Fork the repository and clone your fork.
2. Install the Go version specified in `go.mod`. If the project includes a `toolchain` directive, follow it.
3. Download dependencies and run the tests:

   ```sh
   go mod download
   go test ./...
   ```

4. Create a branch for your change:

   ```sh
   git switch -c fix/short-description
   ```

For larger changes, open an issue first to discuss the approach and scope.

## Reporting bugs

Search existing issues before opening a new report. Include:

- A clear description of the problem.
- Steps or a minimal example to reproduce it.
- Expected and actual behavior.
- Relevant logs and the output of `go version`.
- Your operating system and architecture.

Remove secrets and personal information from logs and examples.

For suspected security vulnerabilities, follow `SECURITY.md` if available. Otherwise, contact the maintainers privately through a contact listed in the repository. Avoid disclosing vulnerability details in a public issue.

## Making changes

Keep each pull request focused on one concern. Follow existing conventions and prefer simple, idiomatic Go.

- Add or update tests for behavior changes and bug fixes.
- Document exported APIs with Go documentation comments.
- Update relevant documentation when usage or behavior changes.
- Handle errors explicitly and provide useful context.
- Avoid unrelated formatting changes and unnecessary dependencies.
- Discuss breaking API changes with maintainers before implementing them.

If you change dependencies, run `go mod tidy` and include the resulting changes to `go.mod` and `go.sum`.

## Checking your work

Format changed Go files:

```sh
gofmt -w path/to/changed.go
```

Run tests and static checks:

```sh
go test ./...
go vet ./...
```

When supported by your platform and the project, check for data races:

```sh
go test -race ./...
```

Follow any additional checks documented in the repository or configured in CI. If a check fails or cannot run in your environment, explain that in your pull request.

## Submitting a pull request

Push your branch to your fork and open a pull request against the repository’s default branch.

Include:

- The problem your change addresses.
- A brief explanation of the solution.
- Related issues, using `Fixes #123` when appropriate.
- Tests and checks you ran.
- Any compatibility changes or known limitations.

Use a descriptive title and clear commit messages. Keep pull requests ready for review, or mark them as drafts while work is in progress.

Maintainers may request changes. Address feedback with additional commits and explain any decisions that need clarification. Ensure required CI checks pass before merging.

## Reviewing and participating

Be respectful, constructive, and specific. Focus feedback on the work, explain your reasoning, and welcome questions from contributors of all experience levels. Follow the repository’s code of conduct, if provided.

## License

By submitting a contribution, you agree that it may be distributed under the project’s license. Only contribute material you have the right to submit.
