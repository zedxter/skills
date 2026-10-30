// document: a memo, article or note. Title block on the first page, no cover, page numbers.
#import "lib.typ": *

#let template(meta, body) = {
  let t = theme-of(meta)
  let lang = meta.at("lang", default: "en")
  show: base.with(t, lang)
  set heading(numbering: if meta.at("numbered", default: false) { "1.1" } else { none })
  show: page-setup.with(t, meta, lang)

  let title = meta.at("title", default: none)
  if title != none and title != "" {
    set par(justify: false)
    block(width: 100%, below: 0.9em, text(font: t.heading, weight: t.heading-weight, size: t.size * 2,
      fill: t.accent, hyphenate: false, title))
    let subtitle = meta.at("subtitle", default: none)
    if subtitle != none and subtitle != "" {
      block(below: 0.8em, text(size: t.size * 1.2, fill: t.muted, hyphenate: false, subtitle))
    }
    block(below: 1.2em, byline(t, meta))
    line(length: 100%, stroke: 0.5pt + t.rule)
    v(0.6em)
  }
  let items = meta.at("highlights", default: ())
  if items.len() > 0 { highlights(t, items); v(1em) }
  if meta.at("toc", default: false) {
    outline(title: word(lang, "contents"), depth: meta.at("toc-depth", default: 2), indent: auto)
    v(1em)
  }
  body
}
