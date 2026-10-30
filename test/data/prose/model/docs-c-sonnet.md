# Release Notes: Version 3.0

Version 3.0 brings dark mode, faster rendering, and a smaller, more modern codebase.

## ✨ New Features

### Dark Mode
Charts now support a built-in dark theme.

- Set `theme: 'dark'` to enable it, or use `theme: 'auto'` to follow the user's system preference (`prefers-color-scheme`).
- All chart elements are themed, including axes, gridlines, legends, tooltips, and default color palettes.
- Colors are tuned for contrast and accessibility on dark backgrounds.
- Custom themes can extend the dark preset, and the theme can be switched at runtime without re-creating the chart.

```js
const chart = new Chart(el, {
  data,
  theme: 'auto'
});

chart.setTheme('dark');
```

## ⚡ Performance

Rendering is significantly faster in 3.0.

- Redraws are more efficient, so interactions such as zooming, panning, and hovering stay smooth.
- Large datasets render more quickly and use less memory.
- Initial render time and animation frame rates are improved.

Actual gains depend on chart type, dataset size, and browser.

## ⚠️ Breaking Changes

### Internet Explorer 11 is no longer supported
IE11 support has been removed. Removing it let us drop legacy polyfills and workarounds, which reduces bundle size and enables the rendering improvements above.

- Supported browsers are the current versions of Chrome, Firefox, Safari, and Edge.
- If you must support IE11, stay on the 2.x line, which will continue to receive critical security fixes for a limited time.

## 🔄 Upgrade Guide

1. Confirm that your supported browsers meet the new requirements.
2. Update the package:
   ```bash
   npm install your-chart-library@3
   ```
3. Review your custom styles. Hard-coded colors may need adjusting if you adopt dark mode.
4. Test your charts, especially if you used custom renderers or relied on IE11-specific behavior.

## Thank You

Thanks to everyone who reported issues, contributed code, and gave feedback during the beta. Please report any problems on our issue tracker.
