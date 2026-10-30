# Platforms

Platform behaviour changes. Each section says when its facts were checked; re-check anything that matters before relying on it, and prefer the platform's own preview over any number here.

## LinkedIn posts

Checked 2026-10-30.

- **Length limit: 3,000 characters.** LinkedIn documents this limit in its help center. The linter's `post-length` rule enforces it.
- **The "see more" cut** is observed, not documented: roughly 140 characters on mobile and 210 on desktop. Check in LinkedIn's composer preview. The first line has to make sense and make the point within that space.
- **Opening**: a statement or a number. Not a question, and not `Here's what…`.
- **Layout**: short paragraphs with blank lines. Markdown syntax doesn't render: headings, bold and `[text](url)` links show as typed. Bare URLs do become links, usually with a preview card. Unicode "bold" letters hurt accessibility (screen readers spell them out); avoid them.
- **Links**: external links in the body are widely reported to reduce reach. If reach matters, put the link in the first comment and say "link in the first comment".
- **Hashtags**: 0–2, at the end. The linter flags more than 2.
- **Mentions**: tag a person or company only when they are part of the story and would welcome it.
- **Ending**: the takeaway, or one specific question. A `P.S.` line is fine for a real follow-up.
- **Engagement bait** is flagged by the linter: "comment X to get Y", "tag someone who", "agree?", "repost if".

## LinkedIn articles

Long-form pieces on LinkedIn's own editor. Write them like a blog article (`articles.md`). Headings and links work. The post that announces the article is a separate post with its own hook, not the article's first paragraph.

## dev.to

Checked 2026-10-30 against the DEV editor guide.

Front matter, documented by DEV:

```yaml
---
title: How we cut CI time from 41 to 9 minutes
published: false
tags: ci, github, devops, testing
canonical_url: https://example.com/blog/ci-time
cover_image: https://example.com/cover.png
series: CI cost
---
```

- `published: false` until the author has looked at the preview. Never set it to `true` for them.
- `tags`: at most four, comma-separated, lowercase, no spaces inside a tag.
- `canonical_url` when the piece first appeared elsewhere (the author's blog). This keeps search engines pointing at the original.
- `cover_image`: the guide recommends 1000 × 420.
- `series`: groups posts; use it only for a real multi-part piece.
- A `description` field is common in the wild but not documented in the editor guide; don't depend on it.
- **Embeds** use Liquid tags, for example `{% embed https://github.com/owner/repo %}`. Use them for GitHub repos, gists and videos rather than bare links when the preview helps.
- **Readers** are developers who skim. Put a 1–3 sentence TL;DR right after the opening. Code blocks are language-tagged and runnable. Show the output when it matters.
- Headings start at `##`; the title is the `#`.

## Hashnode and Medium

Checked 2026-10-30, except where marked.


- **Cross-posting**: set the canonical URL to the original. On Hashnode it's the "originally published at" setting. On Medium, importing a story from a URL sets it. Without a canonical link the copy competes with the original in search.
- **Medium** doesn't reliably convert pasted Markdown (not re-checked): plan to fix formatting in its editor, and check code blocks in the preview. Medium has no tables, so use a list or an image.
- Both allow a subtitle; use it for the concrete promise ("41 → 9 minutes, no tests dropped"), not a second title.

## How-to and life-hack sites

Practical articles in the style of general-audience how-to sites.

- **The promise in the title** is a task the reader can finish: `How to free 20 GB on a full MacBook without deleting photos`.
- **One action per step**, starting with a verb, in the order the reader does them. Name the exact menu path, button or setting.
- **Name tools and prices with a date**: `As of October 2026, the free plan allows 3 projects.` Prices and plan limits go stale first.
- **Say when not to do it** and what can go wrong: data loss, warranty, cost.
- **Screens and versions**: say which OS or app version the steps were checked on.
- No padding intro. One or two sentences on who this is for and what they'll end up with, then step one.

## Company engineering blogs

- **Readers**: peers at other companies, candidates, sometimes customers. The candid parts (what went wrong, the trade-offs) make it worth reading and are also what legal and PR may want removed. Flag them for the author; don't pre-sanitise.
- **Confidential details**: internal names, customer names, revenue and traffic numbers need clearance. The `publication-safety` reviewer checks; the author decides.
- **Diagrams** beat paragraphs for architecture. Describe what the diagram should show if the author will draw it.

## Newsletters

- **Subject line**: specific and honest. It is the title.
- **First line**: the preview text in most email clients. Make it the hook, not "Hi everyone".
- **One main idea per issue**, plus optional short links. Each link gets one sentence on why it's worth the click.
- Links work, and footnote-style source links are welcome.
