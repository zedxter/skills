// onepager: a title band, optional key-figure cards, then the body set as large as still fits the page:
// one column at 13–11 pt, or two columns at 10.5–9.5 pt. More than one page is a finding.
#import "lib.typ": *

#let template(meta, body) = {
  let t = theme-of(meta)
  let lang = meta.at("lang", default: "en")
  show: base.with(t, lang)
  set page(paper: meta.at("paper", default: "a4"), margin: (x: 16mm, top: 14mm, bottom: 16mm),
    footer: context {
      let total = counter(page).final().first()
      if total > 1 { align(center, text(size: 0.8em, fill: t.muted)[#counter(page).get().first() / #total]) }
    })
  show table: it => block(breakable: false, it)  // a table split across the two columns reads badly
  show heading: set block(above: 1.3em, below: 0.6em)

  let band = block(width: 100%, fill: t.accent, inset: (x: 8mm, y: 7mm), radius: 2pt, {
    set par(justify: false)
    set text(fill: white, hyphenate: false)
    text(font: t.heading, weight: t.heading-weight, size: t.size * 2.2, meta.at("title", default: ""))
    let subtitle = meta.at("subtitle", default: none)
    if subtitle != none and subtitle != "" { v(2.5mm); text(size: t.size * 1.2, subtitle) }
    let parts = (meta.at("author", default: none), meta.at("org", default: none), meta.at("date", default: none))
      .filter(x => x != none and x != "")
    if parts.len() > 0 { v(3mm); text(size: t.size * 0.88, parts.join("  ·  ")) }
  })
  let items = meta.at("highlights", default: ())
  let cards = highlights(t, items)
  band
  v(6mm)
  if items.len() > 0 { cards; v(6mm) }

  // Pick the largest setting that fits what is left of the page.
  layout(size => {
    let used = measure(band, width: size.width).height + 6mm
    if items.len() > 0 { used += measure(cards, width: size.width).height + 6mm }
    let avail = size.height - used - 4mm
    let gutter = 8mm
    // (columns, text size, extra space between blocks): the first that fits wins, so a short body gets large
    // type and air, a long one two columns of smaller type.
    let options = ((1, 13pt, 1.6em), (1, 13pt, 1em), (1, 13pt, 0.5em), (1, 12pt, 0.5em), (1, 12pt, 0em),
      (1, 11pt, 0em), (2, 10.5pt, 0em), (2, 10pt, 0em), (2, 9.5pt, 0em))
    let styled(sz, gap) = {
      set text(size: sz)
      set par(spacing: 1.05em + gap * 0.5)
      set list(spacing: 0.7em + gap * 0.25)
      set enum(spacing: 0.7em + gap * 0.25)
      show heading: set block(above: 1.3em + gap, below: 0.6em + gap * 0.3)
      show heading.where(level: 1): set text(size: sz * 1.45)
      show heading.where(level: 2): set text(size: sz * 1.25)
      show heading.where(level: 3): set text(size: sz * 1.1)
      body
    }
    let pick = none
    for o in options {
      let (cols, sz, gap) = o
      let w = if cols == 1 { size.width } else { (size.width - gutter) / 2 }
      let h = measure(block(width: w, styled(sz, gap))).height
      if (if cols == 1 { h } else { h / 2 * 1.1 }) <= avail { pick = o; break }
    }
    if pick == none {
      // Too long for one page even in two small columns: let it flow on (the checker reports the second page).
      columns(2, gutter: gutter, styled(9.5pt, 0em))
    } else {
      let (cols, sz, gap) = pick
      if cols == 1 {
        styled(sz, gap)
      } else {
        // Balance the two columns: Typst would otherwise fill the first to the bottom before starting the second.
        let h = measure(block(width: (size.width - gutter) / 2, styled(sz, gap))).height
        block(height: calc.min(h / 2 * 1.12 + 2em.to-absolute(), avail), columns(2, gutter: gutter, styled(sz, gap)))
      }
    }
  })
}
