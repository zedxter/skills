// report: cover page, contents, numbered headings, running header, "Page X of Y".
#import "lib.typ": *

#let template(meta, body) = {
  let t = theme-of(meta)
  let lang = meta.at("lang", default: "en")
  show: base.with(t, lang)
  set heading(numbering: if meta.at("numbered", default: true) { "1.1" } else { none })

  // Cover: the page has no header or footer and does not count.
  page(header: none, footer: none, margin: (x: 35mm, top: 50mm, bottom: 30mm),
    paper: meta.at("paper", default: "a4"))[
    #set par(justify: false)
    #block(width: 100%, height: 3pt, fill: t.accent)
    #v(18mm)
    #block(width: 90%, text(font: t.heading, weight: t.heading-weight, size: t.size * 2.5, fill: t.accent,
      hyphenate: false, meta.at("title", default: "")))
    #let subtitle = meta.at("subtitle", default: none)
    #if subtitle != none and subtitle != "" {
      v(6mm)
      block(width: 85%, text(size: t.size * 1.35, fill: t.muted, hyphenate: false, subtitle))
    }
    #let items = meta.at("highlights", default: ())
    #if items.len() > 0 { v(14mm); highlights(t, items) }
    #v(1fr)
    #set text(size: t.size * 1.05)
    #let lines = (meta.at("author", default: none), meta.at("org", default: none)).filter(x => x != none and x != "")
    #for l in lines [#l \ ]
    #let d = meta.at("date", default: none)
    #if d != none and d != "" { text(fill: t.muted, d) }
  ]
  counter(page).update(1)

  show: page-setup.with(t, meta, lang)
  if meta.at("toc", default: false) {
    // Set rules, not a wrapper: PDF/UA requires the entry itself to stay a plain link.
    show outline.entry.where(level: 1): set text(weight: "bold")
    show outline.entry.where(level: 1): set block(above: 0.9em)
    outline(title: word(lang, "contents"), depth: meta.at("toc-depth", default: 2), indent: auto)
    pagebreak(weak: true)
  }
  body
}
