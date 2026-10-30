# export

Exports a Postgres table to S3 as gzipped CSV. This is an internal tool for the data team.

## Requirements

- Python 3.11+
- [psycopg 3](https://www.psycopg.org/psycopg3/)
- [boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)

## Installation

[TODO: installation steps (e.g. requirements file, package, or internal distribution)]

## Configuration

| Setting | Source |
|---|---|
| Database connection | `DATABASE_URL` environment variable |
| AWS credentials | The standard boto3 credential chain (environment variables, shared credentials/config files, instance/role credentials, etc.) |
| Target bucket | `--bucket` command-line option (required) |

## Usage

```
python export.py TABLE --bucket BUCKET [--batch-size N] [--since DATE] [--dry-run]
```

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `table` | yes | — | Table name. You can add a schema, e.g. `analytics.events`. |
| `--bucket` | yes | — | Target S3 bucket. |
| `--batch-size` | no | `5000` | Rows per batch. Each batch is written as its own S3 object. |
| `--since` | no | — | Export only rows whose `updated_at` is on or after this ISO date. [TODO: behaviour when the table has no `updated_at` column] |
| `--dry-run` | no | off | Print what would be exported and exit without writing to S3. [TODO: example of dry-run output] |

### Examples

```bash
export DATABASE_URL="postgresql://user:pass@host:5432/dbname"

# Export the full table
python export.py public.orders --bucket my-export-bucket

# Export only rows updated since 1 January 2026, in batches of 10,000
python export.py public.orders --bucket my-export-bucket --since 2026-01-01 --batch-size 10000

# Preview without writing anything
python export.py public.orders --bucket my-export-bucket --dry-run
```

## Output

The tool writes one gzipped CSV object per batch:

```
s3://<bucket>/<table>/<run-id>/<n>.csv.gz
```

- `<table>` is the table name as passed on the command line. [TODO: confirm how schema-qualified names (e.g. `schema.table`) appear in the key]
- `<run-id>` identifies a single run. [TODO: run-id format and how it is generated]
- `<n>` is the batch number. [TODO: whether numbering starts at 0 or 1, and whether it is zero-padded]

[TODO: CSV details such as header row, delimiter, quoting, NULL representation and column order]

## Upgrading to 2.0

Version 2.0 makes `--bucket` required. The tool no longer reads the `EXPORT_BUCKET` environment variable. If your scripts, cron jobs or pipelines relied on `EXPORT_BUCKET`, change them to pass `--bucket` explicitly:

```bash
# Before (1.x)
EXPORT_BUCKET=my-export-bucket python export.py public.orders

# After (2.0+)
python export.py public.orders --bucket my-export-bucket
```

## Exit codes and error handling

[TODO: exit codes, retry behaviour, and what happens to objects already written if a run fails partway]

## Contact

[TODO: owning team / maintainer and support channel]