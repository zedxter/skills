// Shared look for every pdf-typeset template: themes, page furniture, text, tables, code, and the hooks the
// checker reads back (heading positions). No package imports: compiling must never touch the network.

#let themes = (
  classic: (
    body: ("Libertinus Serif", "Source Sans 3"), heading: ("Libertinus Serif", "Source Sans 3"),
    mono: ("DejaVu Sans Mono",), size: 11pt, leading: 0.62em, justify: true,
    accent: rgb("#1f3a5f"), muted: luma(95), rule: luma(200), fill: luma(246),
    heading-weight: "semibold", scale: (1.6, 1.3, 1.1, 1.0),
  ),
  modern: (
    body: ("Source Sans 3", "Libertinus Serif"), heading: ("Source Sans 3", "Libertinus Serif"),
    mono: ("DejaVu Sans Mono",), size: 11pt, leading: 0.7em, justify: false,
    accent: rgb("#0b6e69"), muted: luma(90), rule: luma(205), fill: rgb("#f2f6f6"),
    heading-weight: "bold", scale: (1.7, 1.35, 1.12, 1.0),
  ),
  minimal: (
    body: ("Libertinus Serif", "Source Sans 3"), heading: ("Source Sans 3", "Libertinus Serif"),
    mono: ("DejaVu Sans Mono",), size: 11pt, leading: 0.66em, justify: true,
    accent: luma(25), muted: luma(100), rule: luma(185), fill: luma(245),
    heading-weight: "semibold", scale: (1.5, 1.25, 1.08, 1.0),
  ),
)

// Words the templates print themselves, per document language.
#let words = (
  en: (contents: "Contents", page: "Page", of: "of", figure: "Figure", table: "Table"),
  ru: (contents: "Содержание", page: "Страница", of: "из", figure: "Рисунок", table: "Таблица"),
  uk: (contents: "Зміст", page: "Сторінка", of: "з", figure: "Рисунок", table: "Таблиця"),
  de: (contents: "Inhalt", page: "Seite", of: "von", figure: "Abbildung", table: "Tabelle"),
  fr: (contents: "Table des matières", page: "Page", of: "sur", figure: "Figure", table: "Tableau"),
  es: (contents: "Índice", page: "Página", of: "de", figure: "Figura", table: "Tabla"),
)
#let word(lang, key) = words.at(lang, default: words.en).at(key)

#let theme-of(meta) = themes.at(meta.at("theme", default: "classic"), default: themes.classic)

// A drawn checkbox for task lists: no dependence on a font having ☐ / ☑.
#let checkbox(done) = box(baseline: 0.12em, width: 0.8em, height: 0.8em, stroke: 0.6pt, radius: 1.5pt,
  inset: 0pt, if done { place(center + horizon, text(size: 0.7em, weight: "bold")[✓]) })

#let hr() = align(center, line(length: 30%, stroke: 0.5pt + luma(170)))

// Key figures from the front matter (highlights: [{value, label, note}]): a row of cards. Only figures the user gave.
#let highlights(t, items, size: 1) = if items.len() > 0 {
  grid(columns: (1fr,) * calc.min(items.len(), 4), column-gutter: 4mm, row-gutter: 4mm,
    ..items.map(h => block(width: 100%, fill: t.fill, radius: 2pt, inset: (x: 4mm, y: 3.5mm), {
      set par(justify: false)
      text(font: t.heading, weight: "bold", size: t.size * 2.1 * size, fill: t.accent, hyphenate: false, h.value)
      if h.label != "" { linebreak(); text(font: t.heading, weight: "semibold", size: t.size * size, h.label) }
      if h.note != "" { linebreak(); text(size: t.size * 0.85 * size, fill: t.muted, h.note) }
    })))
}

// Report heading positions for the layout checks (a heading stranded at the bottom of a page, slides that spill).
#let checker-hooks() = context {
  let hs = query(heading).map(h => (level: h.level, page: h.location().page(), y: h.location().position().y / 1pt))
  [#metadata((headings: hs, page-height: page.height / 1pt, pages: counter(page).final().first())) <pdf-typeset-layout>]
}

// Text, paragraphs, headings, lists, tables, code, links, figures: everything but the page itself.
#let base(t, lang, body) = {
  set text(font: t.body, size: t.size, lang: lang, hyphenate: t.justify, number-type: "lining")
  set par(justify: t.justify, leading: t.leading, spacing: 1.05em)
  set list(indent: 0.6em, body-indent: 0.5em, marker: ([•], [–], [·]))
  set enum(indent: 0.6em, body-indent: 0.5em)
  set footnote.entry(separator: line(length: 25%, stroke: 0.5pt + t.rule))
  show heading: set text(font: t.heading, weight: t.heading-weight, fill: t.accent, hyphenate: false)
  show heading: set par(justify: false)
  show heading: set block(sticky: true)
  show heading.where(level: 1): set text(size: t.size * t.scale.at(0))
  show heading.where(level: 1): set block(above: 1.6em, below: 0.8em)
  show heading.where(level: 2): set text(size: t.size * t.scale.at(1))
  show heading.where(level: 2): set block(above: 1.4em, below: 0.7em)
  show heading.where(level: 3): set text(size: t.size * t.scale.at(2))
  show heading.where(level: 3): set block(above: 1.2em, below: 0.6em)
  show heading.where(level: 4): set text(size: t.size * t.scale.at(3), style: "italic")
  show link: set text(fill: t.accent, hyphenate: false)
  show raw: set text(font: t.mono, size: 0.86em)
  show raw.where(block: true): it => block(fill: t.fill, inset: (x: 9pt, y: 7pt), radius: 2.5pt, width: 100%,
    breakable: true, it)
  // highlight, not box: inline code must still break across lines
  show raw.where(block: false): it => highlight(fill: t.fill, extent: 1.5pt, radius: 2pt, top-edge: "ascender",
    bottom-edge: "descender", it)
  show quote.where(block: true): it => block(inset: (left: 12pt, y: 2pt),
    stroke: (left: 1.6pt + t.rule), text(fill: t.muted, it.body))
  set table(stroke: (x, y) => (
    top: if y == 0 { 0.8pt + t.accent } else { none },
    bottom: if y == 0 { 0.6pt + t.accent } else { 0.3pt + t.rule },
  ), inset: (x: 8pt, y: 6pt))
  show table: set text(size: 0.97em, number-width: "tabular")
  show table: set par(justify: false)
  show table.cell.where(y: 0): set text(weight: "semibold")
  show figure: set block(breakable: true)
  set figure(gap: 0.8em)
  show figure.caption: set text(size: 0.9em, fill: t.muted)
  show figure.where(kind: table): set figure.caption(position: top)
  body
}

// Page with a running header (current section on the left, title on the right) and "Page X of Y".
#let page-setup(t, meta, lang, header: true, body) = {
  set page(
    paper: meta.at("paper", default: "a4"),
    margin: (top: 25mm, bottom: 28mm, left: 35mm, right: 35mm),
    header: if header { context {
      let n = counter(page).get().first()
      if n > 1 {
        // The section in force on this page: one that starts here, else the last one before it.
        let sel = heading.where(level: 1, outlined: true)
        let here-page = here().page()
        let starting = query(sel).filter(h => h.location().page() == here-page)
        let before = query(sel.before(here()))
        let current = if starting.len() > 0 { starting.first() } else if before.len() > 0 { before.last() } else { none }
        set text(size: 0.82em, fill: t.muted, font: t.heading, weight: "regular")
        grid(columns: (1fr, auto), gutter: 12pt,
          if current != none { current.body } else { [] },
          meta.at("title", default: ""))
        v(-0.4em)
        line(length: 100%, stroke: 0.4pt + t.rule)
      }
    } },
    footer: context {
      set text(size: 0.82em, fill: t.muted, font: t.heading)
      let n = counter(page).get().first()
      let total = counter(page).final().first()
      if total > 1 { align(center)[#word(lang, "page") #n #word(lang, "of") #total] }
    },
  )
  body
}

// Title, author and date lines under a document's title.
#let byline(t, meta) = {
  let parts = (meta.at("author", default: none), meta.at("org", default: none), meta.at("date", default: none))
    .filter(x => x != none and x != "")
  if parts.len() > 0 { text(fill: t.muted, parts.join("  ·  ")) }
}
