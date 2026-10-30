// slides: 16:9 pages. A title slide from the metadata; every level-1 or level-2 heading starts a new slide
// (level 1 as a section divider when nothing follows it before the next heading). Slide numbers bottom right.
#import "lib.typ": *

#let template(meta, body) = {
  let t = theme-of(meta)
  let lang = meta.at("lang", default: "en")
  show: base.with(t, lang)
  set text(size: 22pt)
  set par(justify: false, leading: 0.6em, spacing: 0.9em)
  set text(hyphenate: false)
  set list(spacing: 0.75em)
  set enum(spacing: 0.75em)
  set page(paper: "presentation-16-9", margin: (x: 18mm, top: 14mm, bottom: 16mm),
    footer: context {
      set text(size: 11pt, fill: t.muted)
      let n = counter(page).get().first()
      if n > 1 {
        grid(columns: (1fr, auto), meta.at("title", default: ""), [#n])
      }
    })
  show heading: set block(above: 0pt, below: 0.9em)
  show heading.where(level: 1): it => {
    pagebreak(weak: true)
    set text(size: 34pt)
    v(1fr)
    it
    block(width: 18%, height: 3pt, fill: t.accent)
    v(1fr)
  }
  show heading.where(level: 2): it => {
    pagebreak(weak: true)
    set text(size: 30pt)
    it
  }
  show heading.where(level: 3): set text(size: 24pt)
  show raw.where(block: true): set text(size: 15pt)
  show table: set text(size: 20pt)
  set table(inset: (x: 10pt, y: 8pt))

  // Title slide.
  {
    set par(justify: false)
    v(1fr)
    text(font: t.heading, weight: t.heading-weight, size: 40pt, fill: t.accent, meta.at("title", default: ""))
    let subtitle = meta.at("subtitle", default: none)
    if subtitle != none and subtitle != "" { v(0.4em); text(size: 22pt, fill: t.muted, subtitle) }
    v(1.2em)
    block(width: 18%, height: 3pt, fill: t.accent)
    v(0.6em)
    let parts = (meta.at("author", default: none), meta.at("org", default: none), meta.at("date", default: none))
      .filter(x => x != none and x != "")
    text(size: 16pt, fill: t.muted, parts.join("  ·  "))
    v(1fr)
  }
  body
}
