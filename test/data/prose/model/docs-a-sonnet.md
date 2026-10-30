# csvdiff

Compare two CSV files and see which rows were added, removed, or changed.

`csvdiff` matches rows by one or more key columns, then reports differences row by row. It is useful for checking data exports, reviewing changes to fixtures, and validating ETL output.

## Features

- Reports **added**, **removed**, and **changed** rows
- Match rows on one or more key columns
- Cell-level detail for changed rows (old value → new value)
- Colored terminal output, plus plain text, JSON, and CSV formats
- Exit code that works in scripts and CI
- Handles large files by streaming where possible

## Installation

Requires Python 3.9 or newer.

```bash
pip install csvdiff
```

Or install from source:

```bash
git clone https://github.com/example/csvdiff.git
cd csvdiff
pip install .
```

## Quick start

Given `old.csv`:

```csv
id,name,price
1,Widget,9.99
2,Gadget,19.99
3,Gizmo,4.50
```

and `new.csv`:

```csv
id,name,price
1,Widget,10.99
3,Gizmo,4.50
4,Doohickey,7.25
```

Run:

```bash
csvdiff old.csv new.csv --key id
```

Output:

```text
+ added   id=4  name=Doohickey, price=7.25
- removed id=2  name=Gadget, price=19.99
~ changed id=1
    price: 9.99 -> 10.99

Summary: 1 added, 1 removed, 1 changed, 1 unchanged
```

## Usage

```text
csvdiff [options] OLD NEW
```

| Argument | Description |
|----------|-------------|
| `OLD` | Path to the original CSV file |
| `NEW` | Path to the updated CSV file |

### Options

| Option | Description |
|--------|-------------|
| `-k, --key COLUMNS` | Comma-separated key column(s) used to match rows. Defaults to the first column. |
| `-f, --format FORMAT` | Output format: `text` (default), `json`, or `csv`. |
| `-d, --delimiter CHAR` | Field delimiter. Defaults to `,`. |
| `-i, --ignore COLUMNS` | Comma-separated columns to exclude from comparison. |
| `--ignore-case` | Compare values case-insensitively. |
| `--trim` | Strip leading and trailing whitespace from values before comparing. |
| `--only {added,removed,changed}` | Show only the given kind of difference. Can be repeated. |
| `--no-header` | Treat files as having no header row. Columns are named `col1`, `col2`, and so on. |
| `--encoding ENCODING` | File encoding. Defaults to `utf-8`. |
| `--color {auto,always,never}` | Control colored output. Defaults to `auto`. |
| `-q, --quiet` | Suppress output and only set the exit code. |
| `-o, --output FILE` | Write the report to a file instead of stdout. |
| `-h, --help` | Show help and exit. |
| `--version` | Show version and exit. |

### Examples

Composite key:

```bash
csvdiff old.csv new.csv --key region,sku
```

Ignore volatile columns:

```bash
csvdiff old.csv new.csv --key id --ignore updated_at,etag
```

Show only changed rows:

```bash
csvdiff old.csv new.csv --key id --only changed
```

Tab-separated files:

```bash
csvdiff old.tsv new.tsv --delimiter $'\t'
```

JSON output for further processing:

```bash
csvdiff old.csv new.csv --key id --format json | jq '.changed'
```

## Output formats

### text

Human-readable output shown above. Added rows start with `+`, removed rows with `-`, and changed rows with `~`.

### json

```json
{
  "added": [{"id": "4", "name": "Doohickey", "price": "7.25"}],
  "removed": [{"id": "2", "name": "Gadget", "price": "19.99"}],
  "changed": [
    {
      "key": {"id": "1"},
      "changes": {"price": {"old": "9.99", "new": "10.99"}}
    }
  ],
  "summary": {"added": 1, "removed": 1, "changed": 1, "unchanged": 1}
}
```

### csv

A single CSV with a leading `_diff` column whose value is `added`, `removed`, or `changed`. Changed rows appear as two lines, the old values and the new values.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Files are identical (under the given options) |
| `1` | Differences were found |
| `2` | Error, such as a missing file, a missing key column, or invalid arguments |

This makes it easy to use in scripts:

```bash
if ! csvdiff expected.csv actual.csv --key id --quiet; then
    echo "Output differs from expected"
fi
```

## Behavior notes

- **Keys must be unique.** If a key appears more than once in a file, `csvdiff` exits with an error and names the duplicate.
- **Columns are matched by name**, not position, so reordering columns is not reported as a change.
- **Added or removed columns** are reported in a header section, and only the columns present in both files are compared per row.
- **Row order is ignored.** Rows are matched by key.
- All values are compared as strings. `1.0` and `1` are different unless you normalize your data first.

## Development

```bash
git clone https://github.com/example/csvdiff.git
cd csvdiff
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Contributing

Issues and pull requests are welcome. Please include a test with any bug fix or new feature.

## License

MIT
