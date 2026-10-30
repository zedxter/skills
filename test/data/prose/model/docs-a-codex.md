# csvdiff

Compare two CSV files and see which rows were added, removed, or changed.

## Installation

From the project directory:

```bash
python -m pip install .
```

## Usage

```bash
csvdiff old.csv new.csv --key id
```

The first file is the original; the second is the updated version. Use `--key` to identify the column that uniquely identifies each row.

For example, given these files:

**old.csv**

```csv
id,name,role
1,Alice,Developer
2,Bob,Designer
```

**new.csv**

```csv
id,name,role
1,Alice,Lead Developer
3,Charlie,Designer
```

Running:

```bash
csvdiff old.csv new.csv --key id
```

prints:

```text
Added:
  id=3: name=Charlie, role=Designer

Removed:
  id=2: name=Bob, role=Designer

Changed:
  id=1:
    role: Developer -> Lead Developer
```

## How comparisons work

- **Added:** A key appears only in the updated file.
- **Removed:** A key appears only in the original file.
- **Changed:** A key appears in both files, but one or more column values differ.

Both files must include a header row and the specified key column. Key values must be unique within each file. Row order does not affect the comparison.

## Help

```bash
csvdiff --help
```
