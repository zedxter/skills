# Dynamic Charts 3.0 Release Notes

Dynamic Charts 3.0 is a performance-focused release. It adds a dark theme, makes rendering faster, and drops support for Internet Explorer 11.

## Highlights

- **Dark mode.** Charts now follow the system color scheme by default and can be set to light or dark explicitly.
- **Faster rendering.** Initial draws and updates are quicker, and large datasets stay responsive.
- **IE11 is no longer supported.** Version 3.0 requires a modern browser.

## Breaking changes

- **Dropped Internet Explorer 11.** Pages that still need IE11 should stay on the 2.x line or add a polyfill-based build step. Check your analytics before upgrading.

## Upgrading

1. Install version 3.0 with your package manager.
2. Remove any IE11-specific polyfills or workarounds that are no longer needed.
3. If you hard-coded light colors, review them and move them to theme tokens so they adapt to dark mode.
4. Re-run your visual tests, since default colors and rendering timing may have changed.

## Thanks

Thanks to everyone who reported rendering issues and tested the dark theme during the beta.
