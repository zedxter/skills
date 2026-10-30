// letter: sender, recipient (in the window of a DIN-format envelope), date, subject, opening, body, closing,
// signature. Front matter: from (lines), to (lines), date, subject, opening, closing, signature.
#import "lib.typ": *

#let template(meta, body) = {
  let t = theme-of(meta)
  let lang = meta.at("lang", default: "en")
  show: base.with(t, lang)
  set page(
    paper: meta.at("paper", default: "a4"),
    margin: (top: 20mm, bottom: 25mm, left: 25mm, right: 20mm),
    footer: context {
      let total = counter(page).final().first()
      if total > 1 {
        set text(size: 0.82em, fill: t.muted)
        align(center)[#word(lang, "page") #counter(page).get().first() #word(lang, "of") #total]
      }
    },
  )
  set par(justify: false)
  let sender = meta.at("from", default: ())
  let recipient = meta.at("to", default: ())

  // A fixed address zone, so the body starts at the same place however long either address is. The recipient
  // sits 45 mm from the top edge and 25 mm from the left, inside the window of a DIN C6/5 or DL envelope.
  block(width: 100%, height: 75mm, {
    if sender.len() > 0 {
      place(top + right, block(width: 70mm, align(right, text(size: 0.92em)[
        #text(weight: "semibold", font: t.heading, fill: t.accent, size: 1.08em, sender.first()) \
        #for l in sender.slice(1) [#text(fill: t.muted, l) \ ]
      ])))
      // The return address line above the recipient, small, as window envelopes show it.
      place(top + left, dy: 20mm, block(width: 85mm, text(size: 0.7em, fill: t.muted,
        sender.slice(0, calc.min(sender.len(), 3)).join(" · "))))
    }
    place(top + left, dy: 25mm, block(width: 85mm, [#for l in recipient [#l \ ]]))
    let d = meta.at("date", default: none)
    if d != none and d != "" { place(bottom + right, d) }
  })
  v(8mm)
  let subject = meta.at("subject", default: none)
  if subject != none and subject != "" {
    block(below: 1.6em, text(weight: "semibold", font: t.heading, size: 1.05em, subject))
  }
  let opening = meta.at("opening", default: none)
  if opening != none and opening != "" { block(below: 1em, opening) }
  body
  let closing = meta.at("closing", default: none)
  if closing != none and closing != "" { block(above: 1.6em, closing) }
  let signature = meta.at("signature", default: none)
  if signature != none and signature != "" {
    v(15mm)  // room for a handwritten signature
    signature
  }
}
