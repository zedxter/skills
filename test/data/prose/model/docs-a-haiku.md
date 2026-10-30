# csvdiff

`csvdiff` compares two CSV files and prints the rows that were added, removed, or changed between them. It's a small, dependency-free command-line tool for checking what changed in exports, data dumps, or fixtures without opening a spreadsheet.

## Features

- Reports rows **added** (present only in the new file), **removed** (present only in the old file), and **changed** (same key, different values)
- Matches rows by a key column you choose, so reordered rows aren't reported as changes
- Shows changed cells side by side, old value and new value
- Works with any CSV dialect the standard library `csv` module accepts, including custom delimiters
- Exits with a status code that makes it easy to use in scripts and CI

## Installation

Requires Python 3.9 or newer.

```bash
pip install csvdiff
```

Or install from source:

```bash
git clone https://github.com/your-org/csvdiff.git
cd csvdiff
pip install .
```

## Usage

```bash
csvdiff OLD.csv NEW.csv --key id
```

Example files:

`old.csv`
```csv
id,name,email,status
1,Alice,alice@example.com,active
2,Bob,bob@example.com,active
3,Carol,carol@example.com,inactive
```

`new.csv`
```csv
id,name,email,status
1,Alice,alice@example.com,active
2,Robert,bob@example.com,active
4,Dave,dave@example.com,active
```

Running the comparison:

```bash
csvdiff old.csv new.csv --key id
```

Output:

```
REMOVED (1)
  id=3  name=Carol  email=carol@example.com  status=inactive

ADDED (1)
  id=4  name=Dave  email=dave@example.com  status=active

CHANGED (1)
  id=2
    name:  Bob -> Robert
```

### Options

| Option | Description |
| --- | --- |
| `OLD` | Path to the original CSV file. |
| `NEW` | Path to the updated CSV file. |
| `-k, --key COLUMN` | Column to match rows on. Required unless `--positional` is used. Repeat the flag for a composite key, e.g. `-k region -k id`. |
| `-d, --delimiter CHAR` | Field delimiter. Default: `,`. Use `'\t'` for TSV. |
| `-e, --encoding NAME` | Text encoding of both files. Default: `utf-8`. |
| `--ignore-columns COLS` | Comma-separated columns to skip when comparing values, e.g. `updated_at,checksum`. |
| `--ignore-case` | Compare values case-insensitively. |
| `--ignore-whitespace` | Strip leading and trailing whitespace from values before comparing. |
| `--format {text,json,csv}` | Output format. Default: `text`. |
| `--only {added,removed,changed}` | Show only the selected categories. Repeatable. |
| `--positional` | Match rows by line position instead of a key column. |
| `--no-header` | Treat the first row as data. Columns are then named `col1`, `col2`, and so on. |
| `-q, --quiet` | Print nothing; rely on the exit code. |
| `-h, --help` | Show help and exit. |
| `--version` | Show the installed version. |

### More examples

Composite key, TSV input, JSON output:

```bash
csvdiff old.tsv new.tsv -d '\t' -k region -k id --format json
```

Ignore timestamp columns and show only changes:

```bash
csvdiff old.csv new.csv -k id --ignore-columns updated_at --only changed
```

Compare files that have no stable key by row position:

```bash
csvdiff old.csv new.csv --positional
```

## Output formats

**`text`** (default) is meant for people. Each section is headed by its category and count. Rows are shown with their key values first.

**`json`** emits one object per run:

```json
{
  "added":   [{"id": "4", "name": "Dave", "email": "dave@example.com", "status": "active"}],
  "removed": [{"id": "3", "name": "Carol", "email": "carol@example.com", "status": "inactive"}],
  "changed": [{"key": {"id": "2"}, "changes": {"name": {"old": "Bob", "new": "Robert"}}}]
}
```

**`csv`** writes one row per difference with a leading `change` column (`added`, `removed`, or `changed`), which is convenient for loading the result into another tool.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The files are identical (after any ignore options are applied). |
| `1` | Differences were found. |
| `2` | Usage or input error, such as a missing file or unknown key column. |

Example in a shell script:

```bash
if csvdiff old.csv new.csv -k id --quiet; then
    echo "No changes"
else
    echo "Changes detected"
fi
```

## Notes and limitations

- Keys must be unique within each file. If a key appears more than once, `csvdiff` exits with code `2` and names the duplicate.
- Files are loaded into memory. For very large files, filter or split them first.
- Column headers must match between the two files. Columns present in only one file are reported as an error unless `--ignore-columns` covers them.
- Comparisons are on the raw text of each cell. Numeric values such as `1.0` and `1` are different unless you normalize them first.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
