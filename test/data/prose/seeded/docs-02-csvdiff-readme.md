# csvdiff

`csvdiff` compares two CSV files by a key column and prints the rows that were added, removed or changed.

## Install

```bash
pip install csvdiff
```

## Usage

```bash
csvdiff old.csv new.csv --key id
```

Output:

```text
+ 1042  Ada Lovelace     ada@example.com
- 0977  Grace Hopper     grace@example.com
~ 0311  email: alan@old.example → alan@example.com
```

## Options

| Option | Default | Description |
|---|---|---|
| `-k`, `--key` | `id` | Column that identifies a row. |
| `--ignore` | none | Column to ignore when comparing. Pass a comma-separated list to ignore several. |
| `-f`, `--format` | `text` | Output format: `text`, `json` or `csv`. |
| `--delimiter` | `,` | Field delimiter. |

Files are read as UTF-8.

## How the comparison works

Under the hood, csvdiff loads both files into hash maps keyed by the key column, which makes lookups O(1). Hash maps are a fundamental data structure: each key is hashed to a bucket, and collisions are resolved by chaining or open addressing, which is why understanding them matters for any developer.

## Exit codes

csvdiff exits with 0 when the files are identical and 1 when it finds differences.
