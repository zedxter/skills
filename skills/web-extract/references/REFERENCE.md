# web-extract reference

Detail behind `SKILL.md`: the fetch pipeline, the warning vocabulary, how bot
walls are detected, what the archive fallback does, and the extraction
post-processing. Nothing here changes how the script is invoked.

## Fetch pipeline

```
live GET ──ok──▶ extract ──text──▶ output (source=live)
   │                └─ empty ──▶ retry with recall ──▶ still empty → exit 1 ──▶ --jina? ──▶ jina
   ├─ blocked (4, not redirect_to_private), HTTP error (3, not 401), network (6)
   │        └──▶ Wayback CDX: newest text/html 200 captures (up to 3), raw `id_` bytes
   │                 ├─ snapshot with challenge markers or < 100 words ──▶ next candidate
   │                 ├─ usable snapshot ──▶ output (source=wayback)
   │                 └─ no snapshot / all unusable / archive down ──▶ --jina? ──▶ jina
   │                                                                      └─ no ──▶ exit with the live code
   └─ not HTML (5), bad input (2), 401, redirect into a private host ──▶ no fallback
```

- One HTTP session per run (curl_cffi), reused for every URL and fallback.
  Cookies live in memory for one run and are never stored.
- Redirects: up to 10 hops, http(s) targets only (libcurl's default would also
  follow `ftp:`); a redirect to another scheme is refused with exit 6
  `redirects`. A redirect from a public host into a private/local one is
  refused with exit 4 `redirect_to_private` (body discarded, no fallback; the
  private host still receives that one request). `redirected` / `downgraded`
  warnings mean the final URL left the requested host or dropped TLS.
- Timeouts: `--timeout` is a wall-clock budget per attempt. The HTTP library
  only bounds connect time and a 1 B/s low-speed window for streamed bodies, so
  the script also checks a monotonic deadline after every chunk; a body that
  keeps dripping is cut at about `--timeout` plus one chunk gap. The library
  adds roughly one second per attempt. A timed-out live fetch is retried once
  (no sleep), so the worst case is about 2 × (S + 1) s. Archive requests use
  `max(S, 45)`, Jina `max(S, 60)`; libcurl additionally enforces a hard
  `max(S, 60)` s ceiling per transfer.
- Retries: one extra attempt, 2 s apart, on connection errors, incomplete
  reads, HTTP 500/502/503/504 without challenge markers, and 429 with
  `Retry-After` ≤ 10 s (sleeping that long). Never on DNS, TLS, redirect or
  impersonation errors, other 4xx, or any challenge classification.
- Body cap: 10 MB decompressed (`size_limit` warning, `partial: true`).
- Honesty: with `--impersonate chrome` the TLS/HTTP2 handshake and client hints
  (`Sec-CH-UA`, `Accept-*`) look like Chrome; the `User-Agent` is always
  `web-extract/1.0 (+https://github.com/zedxter/skills)`. `firefox` sends no
  client hints, so there is no UA/hint mismatch; `none` is plain libcurl with
  the script's own `Accept` headers.

## Privacy

- archive.org sees the full URL, query string included, whenever the fallback
  runs; r.jina.ai sees it only with `--jina`. Do not pass links with signed
  tokens unless `--no-wayback` is set.
- Private hosts never leave the machine: loopback, RFC 1918, link-local,
  reserved and multicast IPs (including shorthand/hex/octal IPv4 literals such
  as `127.1`, `0x7f.0.0.1`, `0177.0.0.1`, which libcurl also accepts),
  `localhost`, `*.localhost`, `*.local`, `*.internal`, `*.lan`, `*.home.arpa`
  and single-label names. Hostnames are matched by suffix only and never
  resolved: a public DNS name that points at 127.0.0.1 or a LAN address
  (`localtest.me`, split-horizon intranet names) is treated as public and may
  be sent to the archive; pass such URLs with `--no-wayback`.
- URLs with embedded credentials (`https://user:pass@host/…`) are rejected
  (exit 2); the script never sends authentication.

## Warnings

Fixed vocabulary of the `warnings` array (`# warning:` lines show the same
names with a hint):

| Warning | Meaning | `partial` |
|---------|---------|-----------|
| `short` | Under 100 words of content, whatever the source (live, archived, Jina, passthrough). | no |
| `low_coverage` | `coverage` under 0.3 on a page with over 300 visible words, or `words` under 60 % of `para_words` when `para_words` ≥ 300: text was dropped, typically a body split around an ad slot the merge did not catch, or a link-list page. Try `--recall`, compare `words`, use `--raw` to see what is missing. | yes |
| `js_shell` | Script-heavy page whose visible text is a footer: metadata is real, the body is a JavaScript shell. | yes |
| `consent_wall` | Final host is `consent.`/`cmp.`/`privacy.`/`cookie(s).`/`gdpr.`, the path contains `collectConsent`/`/consent`, or title + first lines carry two distinct consent-button phrases (under 300 words only). A cookie/consent interstitial, not the article. | yes |
| `challenge_suspected` | A small 2xx body matched a bot-check marker but still extracted; may be a soft interstitial. | no |
| `size_limit` | Body cut at 10 MB. | yes |
| `truncated` | `--max-chars` cut `content`; rerun with `-o`. | yes |
| `links_truncated` | More than 500 links; first 500 kept. | no |
| `links_pruned` | The page has 20+ anchors but fewer than max(5, anchors/10) survived into the main content: link lists (index/landing pages, navigation) are boilerplate to the extractor; `--raw` shows every href. | no |
| `redirected` | Final host differs from the requested one; check `# url`. | no |
| `downgraded` | An https request ended on plain http. | no |
| `archived_copy` | Content is a Wayback snapshot, not the live page. | no |
| `third_party` | Content came from r.jina.ai. | no |
| `binary_saved` | `--raw -o` wrote a binary body; `content` is empty, `saved_to` names the file. | no |
| `feed` | RSS/Atom feed returned as raw XML. | no |
| `author_dropped` | trafilatura reported an author that looked like a page element, a date token or the site name; it was discarded. | no |

`fallback_errors` examples: `wayback-cdx: no snapshot`, `wayback-cdx: HTTP 403
(archive.org restricts CDX lookups for this domain; snapshot index
unavailable)`, `wayback-snapshot 20251115061931: challenge_suspected (21
words)`, `wayback-snapshot 20240604105805: short (10 words)` (a rejected
candidate; the delivered snapshot is never listed), `jina: HTTP 422 …`.

## Block detection

Headers first, status second, body markers last and only under a size guard, so
a long article that merely talks about Cloudflare is never "blocked":

1. `cf-mitigated: challenge` header → exit 4 `cloudflare_challenge`, whatever
   the status or body size.
2. HTTP 202 with an Anubis signature → exit 4 `anubis_challenge`.
3. HTTP 429 → exit 4 `rate_limited` (`retry_after` from the header).
4. HTTP 401 → exit 3 `unauthorized` (no archive fallback: the archive saw the
   login page too).
5. HTTP 403/503 from Cloudflare with a Cloudflare marker, or 403/503/202/200
   with a body ≤ 64 KB matching a verified vendor signature (Cloudflare,
   Anubis) → exit 4; other vendors' markers (Akamai, Imperva, DataDome,
   PerimeterX, Kasada, AWS WAF, Vercel, generic CAPTCHA strings) block only on
   403/503.
6. Bare 403 → exit 4 `forbidden` (an anonymous GET on a public URL answered
   403 is a wall far more often than a permission error, and the agent's
   action is the same).
7. 404/410 → exit 3 `not_found`; other 4xx → `http_error`; 5xx after one retry
   → `server_error`.
8. Any other 2xx/3xx → never blocked; a body under 8 KB with a marker gets
   `challenge_suspected`.

Observed on real sites (2026-10): dblp.org serves an Anubis challenge (HTTP
202) to a datacenter IP; rumble.com answered 200 with impersonation and 403
without from one network on one day; rewe.de returns a 251 KB normal-looking
body under `cf-mitigated: challenge`. Results depend on IP reputation.

## Wayback fallback

- Index lookup via the CDX API (`limit=-3`, `statuscode:200`,
  `mimetype:text/html`, dropped for `--raw`), newest first; the Availability
  API is not used (it rate-limits after one or two calls).
- archive.org refuses CDX queries for some publishers' domains (HTTP 403 "This
  type of CDX query requires authorization", e.g. theguardian.com) although
  snapshots exist; the script then follows the archive's direct redirect to the
  newest capture and otherwise reports `wayback-cdx: HTTP 403 (archive.org
  restricts CDX lookups for this domain; snapshot index unavailable)`. That
  403 comes from archive.org, not from the live site.
- Snapshots are fetched as raw `id_` captures (original bytes, no toolbar, so
  `--links` yields the original absolute URLs); `memento-datetime` gives
  `snapshot`; `final_url` is the archive's original URL.
- Archived pages can be interstitials themselves ("continue shopping", "make
  sure you're not a robot"): a snapshot with challenge markers or under 100
  words is skipped and older candidates are tried; a marker-free short one is
  kept only as a last resort (with `short`, and without a `fallback_errors`
  entry for itself).
- The archive is not tried for exit 1: the Wayback crawler does not run
  JavaScript either. Run `--wayback` yourself when you suspect a consent or
  login page served as 200.
- In `--wayback` mode a CDX or snapshot request that fails with a network error
  or answers 429/502/503/504 is exit 6 `connection` (`archive.org unreachable:
  …`); a private/local or archive.org host is rejected with exit 2 before any
  request.

## Jina (`--jina` only)

`GET https://r.jina.ai/<url>` with `Accept: application/json`, one attempt, 60 s
minimum timeout. The returned markdown is used as-is (`content_format:
"jina_markdown"`, `third_party` warning); `-f text` strips inline links. Jina
renders JavaScript, so it is the right destination for exit 1; it has its own
bot-wall and abuse limits (HTTP 403 for blacklisted domains, 422 for
unresolvable hosts, 20 requests/min per IP). Never send paywalled, personal or
tokenised URLs; ask the user first.

## Extraction and post-processing

- trafilatura runs once on the bytes with the final URL (relative links resolve
  against the live site). Before that the page is parsed once: every `<pre>` is
  flattened so syntax-highlighted code keeps its line breaks, and same-class
  sibling body containers (an article split into two `div.post-content` blocks
  around an ad slot) are merged, because trafilatura keeps only the first
  container each body selector matches. MediaWiki section-edit links, footnote
  markers and inline templates are pruned by class name.
- Empty results are retried with `favor_recall`; the longer of the two passes
  wins. `--recall`/`--precision` set the corresponding trafilatura flags.
- "Nothing extracted" (exit 1) is the script's rule: no document, under 40
  characters or 5 words, a `<noscript>` asking for JavaScript or an empty
  `#root`/`#app`/`#__next` container (`js_shell`), or an empty/whitespace-only
  body (including 204/205). The result still carries `title`, `description`,
  `sitename`, `language`, `body_words` and `para_words` when the HTML had them.
- Markdown post-processing (not applied to `-f text`, which trafilatura already
  normalises): `<sup>`/`<sub>` remnants fixed, MediaWiki `[edit]`, `[citation
  needed]`, `[clarification needed]`, `[when?]`, `[who?]`, `[which?]` lines
  removed (other bracketed lines such as `[Applause]` are content and stay),
  source-HTML indentation inside inline-formatted runs collapsed (nested list
  indents and fenced code untouched), same-page anchor links (`href="#x"`,
  which trafilatura turns into `https://host#x`) unwrapped to their text,
  paragraphs over 40 characters repeated verbatim deduplicated (responsive
  desktop+mobile markup), headings with no body before an equal-or-higher
  heading removed. Not handled: dismissible banners and modal copy that carry
  text, accordion repeats whose copies differ slightly, dynamically loaded
  sections.
- `--links`: links from the extracted main content only, absolute, deduped by
  URL keeping the first text, `javascript:`/`mailto:`/`tel:` and same-page
  anchors skipped, empty or arrow-only (`↑`) texts skipped, max 500.
- Metadata: `title` prefers H1/`og:title` over `<title>`; `date` comes from
  htmldate (may be a creation or modification date); `author` passes a junk
  filter (navigation labels, date tokens, the site name, bare abbreviations;
  dropped values add `author_dropped`); `language` is the first two letters of
  `<html lang>`. All page-derived header fields are whitespace-collapsed and
  stripped of control characters so a page cannot forge header lines.
- Coverage: `body_words` counts visible words after dropping
  `script`/`style`/`noscript`/`template`; `para_words` counts words in `<p>`
  elements of 8+ words; `coverage = words / body_words` capped at 1.0.
- Feeds: RSS/Atom/RDF bodies are passed through as raw XML with `title` = the
  feed's title; large feeds exceed the 60 000-char stdout cap, so use `-o
  feed.xml` or `--max-chars 0` and parse the entries yourself. Passthrough
  encodings: header charset, in-document declaration, UTF-8, detection,
  cp1252; short legacy-encoded bodies without a declaration may decode wrongly.
