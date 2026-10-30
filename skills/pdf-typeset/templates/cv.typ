// cv: JSON Resume, prepared by the script (dates formatted, empty sections dropped). Strings are content, never
// markup.
#import "lib.typ": *

#let template(meta, d) = {
  let t = theme-of(meta)
  let lang = meta.at("lang", default: "en")
  show: base.with(t, lang)
  set par(justify: false, leading: 0.72em)
  set list(spacing: 0.75em, marker: text(fill: t.accent)[•])
  set page(paper: meta.at("paper", default: "a4"), margin: (x: 20mm, top: 18mm, bottom: 18mm),
    footer: context {
      let total = counter(page).final().first()
      if total > 1 {
        set text(size: 0.8em, fill: t.muted)
        grid(columns: (1fr, auto), d.name, [#counter(page).get().first() / #total])
      }
    })

  // Header: name, role, contacts, each its own block so the gaps between them are exact.
  block(below: 0pt, text(font: t.heading, weight: "bold", size: 2.6em, fill: luma(20), d.name))
  if d.label != "" { block(above: 1.7em, below: 0pt, text(font: t.heading, size: 1.3em, fill: t.accent, d.label)) }
  if d.contacts.len() > 0 {
    block(above: 1.3em, below: 0pt, text(size: 0.92em, fill: t.muted,
      d.contacts.map(c => if c.at(1) != "" { link(c.at(1), c.at(0)) } else { [#c.at(0)] })
        .join(h(0.5em) + [·] + h(0.5em))))
  }
  block(above: 1.3em, below: 1.3em, line(length: 100%, stroke: 1pt + t.accent))
  if d.summary != "" { block(below: 0pt, text(size: 1.05em, d.summary)) }

  let section(g, title, body) = {
    block(above: 1.9em + g, below: 0.9em + g * 0.3, sticky: true,
      text(font: t.heading, weight: "bold", fill: t.accent, size: 0.92em, tracking: 0.08em, title))
    body
  }
  let entry(g, e) = block(below: 1.35em + g * 0.5, breakable: false, {
    grid(columns: (1fr, auto), column-gutter: 6mm,
      text(font: t.heading, weight: "semibold", size: 1.08em, if e.subtitle != "" { e.subtitle } else { e.title }),
      text(fill: t.muted, size: 0.92em, e.dates))
    let second = (if e.subtitle != "" { e.title } else { "" }, e.place).filter(x => x != "")
    if second.len() > 0 { block(above: 0.65em, below: 0em, text(fill: t.muted, second.join("  ·  "))) }
    if e.summary != "" { block(above: 0.7em, below: 0em, e.summary) }
    if e.highlights.len() > 0 { block(above: 0.85em, list(..e.highlights.map(h => [#h]))) }
  })
  let sections(g) = for s in d.sections {
    if s.kind == "entries" {
      section(g, s.title, for e in s.items { entry(g, e) })
    } else if s.kind == "pairs" {
      section(g, s.title, grid(columns: (auto, 1fr), column-gutter: 6mm, row-gutter: 0.8em + g * 0.2,
        ..s.items.map(p => (text(weight: "semibold", p.at(0)), p.at(1))).flatten()))
    }
  }
  // A short CV gets more air between sections instead of an empty bottom half; a long one stays compact.
  // The first section's space above is lost at the top of the layout container, so give it explicitly.
  layout(size => {
    let room = size.height - here().position().y - 6mm
    let pick = 0em
    for g in (1.6em, 1.2em, 0.8em, 0.4em) {
      if measure(block(width: size.width, { v(1.9em + g); sections(g) })).height <= room { pick = g; break }
    }
    v(1.9em + pick)
    sections(pick)
  })
}
