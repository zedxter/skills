# csvdiff

csvdiff compares two CSV files and prints the rows that were added, removed, or changed between them. It matches rows by a key column, so a reordered file does not produce a wall of false differences.

## Installation

csvdiff needs Python 3.9 or later and has no dependencies outside the standard library.

```
pip install csvdiff
```

## Basic use

```
csvdiff old.csv new.csv --key id
```

Given these two files:

```
# old.csv
id,name,price
1,Widget,4.00
2,Gadget,9.50
3,Gizmo,12.00
```

```
# new.csv
id,name,price
1,Widget,4.25
3,Gizmo,12.00
4,Doohickey,7.75
```

the output is:

```
- 2,Gadget,9.50
+ 4,Doohickey,7.75
~ 1: price 4.00 -> 4.25
```

A line starting with `-` is a row that exists only in the first file. A line starting with `+` exists only in the second. A line starting with `~` is a row whose key appears in both files but whose other values differ; it shows the key and each column that changed, with the old and new value.

The exit status is 0 when the files match, 1 when they differ, and 2 on an error such as a missing file or a key column that does not exist. This makes it usable in shell scripts and CI checks without parsing the output.

## Choosing the key

`--key` takes a column name. If rows are identified by more than one column, pass a comma-separated list:

```
csvdiff old.csv new.csv --key region,sku
```

If you leave `--key` out, csvdiff uses the first column. It will stop with an error if that column contains duplicate values, since there is no sensible way to match such rows. If you need to compare files with no unique key, use `--no-key`, which compares rows as whole lines and reports only additions and removals. A changed row will then appear as one removal and one addition.

## Options

`--key COLS`
Column or columns that identify a row.

`--no-key`
Compare whole rows. Cannot be combined with `--key`.

`--ignore COLS`
Skip these columns when looking for changes. Useful for timestamps or auto-generated IDs that differ on every export.

`--delimiter CHAR`
Field separator. The default is a comma. Use `--delimiter '\t'` for tab-separated files.

`--no-header`
Treat the first line as data. Columns are then named `1`, `2`, `3`, and so on, and `--key` and `--ignore` refer to those numbers.

`--trim`
Strip leading and trailing whitespace from every value before comparing.

`--format FORMAT`
Output style. `text` is the default shown above. `json` prints one object per difference, which is easier to feed into other tools. `csv` writes a CSV with an extra first column named `status` containing `added`, `removed`, or `changed`.

`--color auto|always|never`
Whether to color the output. The default, `auto`, colors only when writing to a terminal.

`--quiet`
Print nothing and rely on the exit status.

## Behavior worth knowing

Values are compared as strings. `4.0` and `4.00` are different, as are `1` and `01`. If your files differ only in number formatting, normalize them first.

Both files must have the same header, apart from column order. If a column exists in one file and not the other, csvdiff reports an error and does not guess. Pass `--ignore` with the column name to proceed anyway.

Files are read fully into memory, indexed by key. A file with a few million rows works fine on an ordinary laptop. Files much larger than your available memory will not.

Rows in the output appear in the order of the first file for removals and changes, followed by additions in the order of the second file.

## Reporting problems

Open an issue with the two input files, or a reduced version of them, and the command you ran. Most bugs turn out to depend on one odd row, so a small reproducing sample saves a lot of back and forth.

## License

MIT
