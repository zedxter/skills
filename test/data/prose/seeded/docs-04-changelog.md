# Changelog

## [3.0.0]

### Added
- Dark mode for all chart types (`theme: "dark"`).
- `chart.exportSVG()` returns the chart as an SVG string.

### Changed
- Internet Explorer 11 is no longer supported.
- Rendering is faster: the line chart with 100,000 points now draws in 180 ms instead of 1.4 s.
- Refactored the internal layout engine.

### Fixed
- Tooltips no longer flicker when the pointer crosses two series at once.

## [2.9.1] - 2026-07-14

### Fixed
- Legends wrapped one item too early on narrow screens.
