# Changelog

## [2.0.0] - 2026-10-20
### Added
- `--since` exports only rows updated on or after a date.
- Streaming writer: memory use stays flat for large tables.
### Changed
- **Breaking:** `--bucket` is now required; the `EXPORT_BUCKET` environment variable is no longer read.
- Default `--batch-size` raised from 1000 to 5000.
### Fixed
- A retried batch no longer duplicates rows (batches carry a run id).
