// invoice: every number arrives as finished text from the script (totals computed with Decimal there);
// this file only lays it out. Strings inserted as content are never parsed as markup.
#import "lib.typ": *

#let template(meta, d) = {
  let t = theme-of(meta)
  let lang = meta.at("lang", default: "en")
  let L = d.labels
  show: base.with(t, lang)
  set par(justify: false)
  set text(size: t.size * 1.02)
  set table(inset: (x: 7pt, y: 8pt))
  show table: set text(size: 1em)
  set page(paper: meta.at("paper", default: "a4"), margin: (x: 20mm, top: 20mm, bottom: 22mm),
    footer: {
      set text(size: 0.78em, fill: t.muted)
      line(length: 100%, stroke: 0.4pt + t.rule)
      v(-0.3em)
      d.footer.join("  ·  ")
      context {
        let total = counter(page).final().first()
        if total > 1 { h(1fr); [#counter(page).get().first() / #total] }
      }
    })

  let lines-of(xs) = xs.map(x => [#x]).join(linebreak())
  let s = d.seller
  let b = d.buyer

  grid(columns: (1fr, auto), gutter: 10mm,
    text(font: t.heading, weight: "bold", size: 2.8em, fill: t.accent, d.title),
    align(right, {
      text(weight: "bold", font: t.heading, size: 1.2em, s.name)
      linebreak()
      set text(size: 0.88em, fill: t.muted)
      lines-of(s.lines)
    }),
  )
  v(14mm)
  grid(columns: (1fr, auto), gutter: 10mm,
    {
      text(size: 0.78em, fill: t.muted, weight: "semibold", L.bill-to)
      linebreak()
      v(0.2em)
      text(weight: "bold", size: 1.12em, b.name)
      linebreak()
      lines-of(b.lines)
    },
    grid(columns: (auto, auto), column-gutter: 6mm, row-gutter: 0.75em,
      ..d.facts.map(f => (text(fill: t.muted, f.at(0)), text(weight: "semibold", f.at(1)))).flatten()),
  )
  v(12mm)

  table(
    columns: (1fr, auto, auto, auto),
    align: (left, right, right, right),
    table.header(L.description, L.qty, L.unit-price, L.amount),
    ..d.rows.map(r => (r.description, r.qty, r.price, r.amount)).flatten(),
  )
  v(5mm)
  align(right, block(width: 55%, {
    grid(columns: (1fr, auto), align: (left, right), row-gutter: 0.8em, column-gutter: 8mm,
      ..d.totals.map(r => (text(fill: t.muted, r.at(0)), r.at(1))).flatten())
    v(0.5em)
    block(width: 100%, fill: t.accent, inset: (x: 4mm, y: 4mm), radius: 2pt,
      grid(columns: (1fr, auto), align: (left + horizon, right + horizon), text(fill: white, weight: "bold", L.total),
        text(fill: white, weight: "bold", size: 1.35em, d.total)))
  }))
  // Payment details and notes sit at the foot of the page, where an invoice's reader looks for them.
  v(1fr)
  grid(columns: (1fr,) * calc.max(d.sections.len(), 1), column-gutter: 10mm,
    ..d.sections.map(sec => {
      block(below: 0.6em, text(size: 0.8em, fill: t.muted, weight: "semibold", tracking: 0.04em, sec.at(0)))
      lines-of(sec.at(1))
    }))
  v(6mm)
}
