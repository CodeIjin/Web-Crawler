# Official axe-core Engine

- **Source Repository**: [https://github.com/dequelabs/axe-core](https://github.com/dequelabs/axe-core)
- **Engine**: Deque Systems axe-core JavaScript Engine (`axe.min.js`)
- **Version**: 4.14.0
- **License**: Mozilla Public License 2.0 (MPL-2.0)

## Overview
This directory contains the official standalone JavaScript bundle of Deque's `axe-core`.
Instead of relying on unmaintained third-party Python wrappers, `wcag_scan.py` directly injects this official `axe.min.js` bundle into Playwright browser pages and executes `axe.run()` natively.

## Updating axe-core
To update to the latest release of `axe-core` at any time, run:
```bash
python wcag_scan.py --update-axe
```
This downloads the latest official production build from Deque's release distribution.
