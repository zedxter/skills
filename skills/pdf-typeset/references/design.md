# Looking at the pages

The checks catch broken output. This list covers what only looking catches. Open
`contact-sheet.png` for the whole document, then each `page-NN.png` that
matters. Fix the content or the template choice, not the template.

## The numbers the templates keep

The templates are built to these rules. Check them again when you change a
theme or write a `.typ` yourself.

- **Type:** body 10.5–12 pt; one modular scale for headings (about 1.2–1.3
  between levels), with three or four sizes in all; line spacing 1.3–1.45 of
  the type size.
- **Measure:** 60–80 characters per line for running text. The flowing
  templates use about 140 mm of text width on A4.
- **Margins:** at least 20 mm; the bottom margin no smaller than the top.
- **Colour:** one accent colour. Text contrast at least 4.5:1 against its
  background (white on the accent bands passes).
- **Language typography:** typographic quotes come from `lang`. Dashes and
  non-breaking spaces are up to the text: type `—` and `–` rather than `--`,
  and put a non-breaking space (U+00A0) between a number and its unit
  (`41 200 €`, `3 ч`) so they never split across lines.

## First glance

- **One thing stands out on each page.** On a cover it is the title, on an
  invoice the total, on a slide the slide's point. If two things compete, cut
  or demote one: a heading level down, or bold removed.
- **Even grey.** Squint at the page. Dense black blocks (too much bold, a full
  grid) and holes (a lone line, a half-empty page in the middle) both show up.
  Long runs of bold or capitals are the usual cause.
- **Nothing cramped against an edge.** Tables, images and code blocks stay
  inside the text column.

## Text

- **Headings:** two or three levels are enough, and no heading should sit
  alone at the bottom of a page. A heading followed by a single line looks
  unfinished: merge the section or write the paragraph.
- **Paragraphs:** three to eight lines read best. A wall of fifteen lines
  wants a break; a run of one-line paragraphs wants joining or turning into a
  list.
- **Lists:** items of similar length and grammar. A list of one item is a
  sentence.
- **Justified text** (the `classic` and `minimal` themes) should show no
  rivers or wide gaps. If a line looks gappy, there is usually a long unbroken
  word or URL in it: shorten the URL or put it in a footnote.
- **Language:** `lang` must match the text. It controls hyphenation, quotes and
  labels, so Russian text with `lang: en` hyphenates wrongly.

## Tables

- Numbers right-aligned in their own column, with the same decimals.
  Convert the units into one column header ("Cost, €") instead of repeating
  them in every cell.
- At most about six columns on a portrait page. A wider table runs off the
  page (the `overflow` check): split it or transpose it.
- A table of two rows is often clearer as a sentence.

## Images

- Sharp at print size (the `low-res-image` check flags under 120 dpi).
- Charts readable: axis labels at least the size of the caption text.
- Every figure that needs explaining has a caption (`![alt](file.png "Caption")`).

## Per template

- **report:** the cover shows title, subtitle, author or organisation and
  date, and nothing else. Contents only when there are four or more sections.
  The running header names the current section, so short section names read
  better there.
- **letter:** recipient block, then date, then subject, then body. One page
  when possible. The signature name matches the sender.
- **invoice:** seller, buyer, number, date and due date all visible without
  hunting. Line descriptions say what was delivered. The total band is the
  strongest element. Payment details (IBAN, reference) are present.
- **cv:** one page for under ten years of experience, two at most. Recent
  roles get more lines than old ones. Highlights start with a verb and include
  a number where there is one.
- **onepager:** fits one page with a little air. The title band says what it
  is about in plain words. The most important number is in the first lines.
- **slides:** one idea per slide, at most six bullets of one line each. A
  table of more than five rows belongs in a handout.

## Accessibility and metadata

Title, author and language come from the front matter, and the PDF is tagged
and bookmarked by default. Images need meaningful alt text: describe what
the chart shows, not "chart".
