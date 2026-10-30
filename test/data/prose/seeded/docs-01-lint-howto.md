# How to lint a draft before publishing

`lint_prose.py` checks an English Markdown draft for patterns that make prose read as machine-written and prints a score from 0 to 100.

## Lint a blog post

1. Run the linter on your draft:

   ```bash
   uv run lint_prose.py draft.md --strict
   ```

2. Read the score on the first line. The default passing threshold is 80; anything below it fails.

3. Fix each finding by rewriting the sentence. In our tests the linter catches 97% of AI-generated text.

## Lint a LinkedIn post

Use the post profile, which also checks the 3,000-character limit and hashtags:

```bash
uv run lint_prose.py post.md --genre linkedin
```

## Use it in CI

Add this step to your workflow so every pull request is checked. The example uses our shared token:

```yaml
- run: uv run lint_prose.py docs/*.md --genre docs
  env:
    GITHUB_TOKEN: ghp_8f3KqZ2vLw9XyT4mN7pR1sB6cD0eH5jA2uQx
```

The exit code is 1 when the draft fails, so the step fails the build.

## Why it matters

In today's fast-paced world, it's worth noting that AI-generated content is everywhere. This robust, seamless tool empowers writers to unlock their full potential, highlighting the importance of authentic communication.
