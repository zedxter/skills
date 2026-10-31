# Prose corpora for `human-writing`

Used by `test/test_human_writing.py` to measure the linter's false positives and catch rate.

- `human/`: 20 texts written by people at 18F, the US government's digital services team. Both source repositories are works of the US government in the public domain, with copyright waived worldwide under CC0 1.0. Each file starts with a comment linking its source at a pinned commit.
  - `article-*`: ten blog posts published in 2017–2018 (`18F/18f.gsa.gov`, `content/posts/`).
  - `docs-*`: ten pages of the 18F guides (`18F/guides`, `content/`). Most were migrated into that repository in 2023 from older guides and edited since. `docs-02-secrets` (2024-06) and `docs-05-release-checklist` (2024-10) were first written later, so treat those two as weaker evidence of human-only writing.
  - The site front matter was removed. Some Eleventy/Liquid template expressions remain inside link targets; the linter masks them with the link. The text is otherwise unchanged.
  - All twenty come from one organisation's house style, so passing them shows the linter tolerates careful plain-language prose, not every human style.
- `human/` also holds 20 texts from two programming-language projects, added to widen the styles. Each file's first line links its source at a pinned commit and names the licence.
  - `article-11` … `article-18`: eight Go blog posts (`golang/website`, CC BY 4.0, The Go Authors).
  - `article-19` … `article-24`: six Rust blog posts (`rust-lang/blog.rust-lang.org`), and `docs-11` … `docs-16`: six Rust RFCs (`rust-lang/rfcs`), both MIT / Apache-2.0, The Rust Project Developers.
- `slop/`: 10 texts written for this test in the default style of a chat model: four articles, three LinkedIn-style posts, three docs pages. The file name prefix (`article-`, `post-`, `docs-`) is the genre the test lints it with.

The default threshold must pass every human text and fail at least 80% of the slop texts.
