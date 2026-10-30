# export

Copies rows from a Postgres table to S3 as gzipped CSV. Internal tool for the data team.

## Requirements

- Python 3.11+
- [psycopg 3](https://www.psycopg.org/psycopg3/)
- [boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)

## Installation

[TODO: installation steps, e.g. a requirements file, package name or where to get `export.py`]

## Configuration

| Setting | Source |
|---|---|
| Database connection | `DATABASE_URL` environment variable |
| AWS credentials | boto3's standard credential chain (environment variables, `~/.aws/credentials`, instance/role credentials, etc.) |
| Target bucket | `--bucket` argument (required) |

```sh
export DATABASE_URL="postgresql://user:password@host:5432/dbname"
```

## Usage

```sh
python export.py TABLE --bucket BUCKET [--batch-size N] [--since DATE] [--dry-run]
```

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `table` | yes | | Table name, optionally schema-qualified (e.g. `public.orders`) |
| `--bucket` | yes | | Target S3 bucket |
| `--batch-size` | no | `5000` | Rows per batch |
| `--since` | no | | Export only rows whose `updated_at` is on or after this ISO date |
| `--dry-run` | no | off | Print what would be exported, then exit without writing anything |

`--since` filters on an `updated_at` column. [TODO: what happens if the table has no `updated_at` column; whether date-times and timezones are accepted or only dates]

### Examples

Export a whole table:

```sh
python export.py public.orders --bucket my-data-exports
```

Export rows updated since 1 October 2026, 10,000 rows per batch:

```sh
python export.py public.orders --bucket my-data-exports --since 2026-10-01 --batch-size 10000
```

Check what would be exported without writing to S3:

```sh
python export.py public.orders --bucket my-data-exports --dry-run
```

## Output

Each batch is written to S3 as its own gzipped CSV object:

```
s3://<bucket>/<table>/<run-id>/<n>.csv.gz
```

- `<table>`: the table name as passed on the command line [TODO: confirm whether the schema prefix is kept in the key]
- `<run-id>`: an identifier for this run [TODO: format and how it is generated]
- `<n>`: the batch number [TODO: whether numbering starts at 0 or 1, and whether it is zero-padded]

[TODO: CSV details such as the header row, delimiter, quoting and NULL representation]

## Upgrading from 1.x

Version 2.0 made `--bucket` required. The `EXPORT_BUCKET` environment variable is **no longer read**. Scripts or jobs that relied on it must now pass `--bucket` explicitly:

```sh
# 1.x
EXPORT_BUCKET=my-data-exports python export.py public.orders

# 2.0+
python export.py public.orders --bucket my-data-exports
```

## Error handling and exit codes

[TODO: behaviour on failure partway through a run (are partial uploads cleaned up?), retries and exit codes]

## Ownership and support

[TODO: owning team or contact, and where to report issues]