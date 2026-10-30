# Release Notes: Version 3.0

Version 3.0 changes how the library draws, what it runs on, and how it looks at night. Some of this will require action on your part, so the breaking change comes first.

## Breaking change: Internet Explorer 11 is no longer supported

We have removed the polyfills, the fallback code paths and the IE11 test runs. The bundle no longer includes the `Object.assign` and `Array.from` shims, and the build target is now ES2018. If you still have to serve IE11 users, stay on the 2.x line. We will keep applying security fixes to 2.x until the end of next year, but it will get no new features.

If you do not need IE11, upgrading should be uneventful. The public API is otherwise unchanged from 2.9, and the deprecated options that were flagged in 2.7 are the only other things removed.

## Dark mode

Charts can now render against a dark background. Set `theme: 'dark'` in the chart options, or set `theme: 'auto'` to follow the user's `prefers-color-scheme` setting. In auto mode the chart responds if the setting changes while the page is open, so there is no need to reload.

The dark theme is more than an inverted palette. Gridlines, axis labels and tooltips were adjusted separately so that they stay readable without glowing against the background. Series colors were retuned to keep enough contrast on dark surfaces, and the default palette still distinguishes adjacent series for people with common forms of color blindness. If you define your own palette, you can supply a second set of colors under `palette.dark`. If you omit it, your light colors are used as they are, so check them against a dark background before shipping.

## Faster rendering

The renderer has been rewritten to batch its drawing operations and to skip work for elements that have not changed. In our benchmark suite, a line chart with 50,000 points now draws in roughly a third of the time it took in 2.9. Redraws on resize and on data updates improved more than first draws did, because unchanged axes and labels are no longer rebuilt.

Memory use during animation is also lower, since the renderer reuses objects between frames instead of allocating new ones. Small charts with a few dozen points will not feel any different, so do not expect a visible change there.

One side effect is worth knowing about. Custom plugins that reach into the old `renderer.draw()` internals will no longer work, because that method now queues work instead of executing it immediately. Plugins that use the documented `afterDraw` hook are unaffected. The migration guide has a short section on converting internal calls.

## Upgrading

Run `npm install chartkit@3`, remove any IE11-specific build configuration, and run your visual tests. Snapshot tests may show small differences in antialiasing, since the new renderer rounds some coordinates differently. The full list of changes is in the changelog.
