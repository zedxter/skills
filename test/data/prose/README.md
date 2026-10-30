# Prose corpora for `prose-writer`

Used by `test/test_prose_writer.py` to measure the linter's false positives and catch rate.

- `human/`: 20 texts written by people before 2022. Ten are blog posts from 18F (`18F/18f.gsa.gov`, `content/posts/`) and ten are pages of the 18F guides (`18F/guides`, `content/`). Both repositories are works of the US government in the public domain, with copyright waived worldwide under CC0 1.0. Each file starts with a comment that links its source. Hugo shortcodes and the site front matter were stripped; the text is otherwise unchanged.
- `slop/`: 10 texts written for this test in the default style of a chat model: four articles, three LinkedIn-style posts, three docs pages. The file name prefix (`article-`, `post-`, `docs-`) is the genre the test lints it with.

The default threshold must pass every human text and fail at least 80% of the slop texts.
