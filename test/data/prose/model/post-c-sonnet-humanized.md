We just open-sourced the feature-flag library we've been using internally for the past four years. The repo is public as of today.

It started as a small wrapper around a config file. Someone on the team got tired of redeploying every time we wanted to turn a feature off, so they wrote about 200 lines to read flags from a database and cache them in memory. Over time other teams started using it, and the 200 lines turned into something with percentage rollouts, per-user overrides, and an audit log that records who changed what and when.

The audit log is the part I'd point to first. Twice in the last two years it settled an argument about whether a flag had been flipped before or after an incident. Having a timestamped answer saved us a long meeting both times.

We also spent a lot of time on the boring failure cases. If the flag store goes down, the library serves the last values it saw instead of throwing errors or falling back to defaults that might be wrong. Evaluation happens locally, so a flag check adds no network call to a request. We learned most of this by getting it wrong first.

It's written in Go, with client bindings for Python and TypeScript. The documentation covers setup, the evaluation rules, and a few migration notes for people coming from hosted flag services. We kept the dependency list short on purpose, because we didn't want adopting it to mean adopting a pile of other packages.

We cleaned out internal references and added tests where coverage was thin before publishing, so it should be usable without much reading of the source. If you try it and something breaks or confuses you, open an issue. Pull requests are welcome too, though we'd ask you to start with an issue for anything large.

Link to the repo is in the first comment.
