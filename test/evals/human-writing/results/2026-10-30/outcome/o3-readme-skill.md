# export

`export.py` copies rows from a Postgres table to S3 as gzipped CSV. It's an internal tool for the data team.

## Requirements

- Python 3.11 or later
- psycopg 3
- boto3

[TODO: how to install (requirements file, package name or pip command), and whether `python export.py` is the right way to run it]

## Setup

Set `DATABASE_URL` to the connection URL of the database you're exporting from:

```sh
export DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
```

The tool gets AWS credentials the usual boto3 way. See the [boto3 credentials guide](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html).

[TODO: database privileges the `DATABASE_URL` user needs, and S3 permissions the AWS credentials need on the bucket]

## Usage

```sh
python export.py table --bucket BUCKET [--batch-size N] [--since DATE] [--dry-run]
```

Export a whole table:

```sh
python export.py analytics.events --bucket my-bucket
```

Export only rows whose `updated_at` is on or after a date:

```sh
python export.py analytics.events --bucket my-bucket --since 2026-01-01
```

Print what would be exported and exit:

```sh
python export.py analytics.events --bucket my-bucket --dry-run
```

[TODO: example output of a run and of `--dry-run`]

## Options

| Argument | Required | Default | Description |
|---|---|---|---|
| `table` | yes | | Table name, optionally schema-qualified (`schema.table`). |
| `--bucket` | yes | | Target S3 bucket. Replaces the `EXPORT_BUCKET` variable used before 2.0. |
| `--batch-size` | no | `5000` | Rows per batch. Each batch is written as one S3 object. |
| `--since` | no | | Only export rows whose `updated_at` is on or after this ISO date. [TODO: what happens if the table has no `updated_at` column] |
| `--dry-run` | no | | Print what would be exported and exit. |

## Output

Each batch becomes one S3 object:

```text
<table>/<run-id>/<n>.csv.gz
```

[TODO: confirm `<n>` is the batch number and whether it starts at 0 or 1, whether `<table>` keeps the schema prefix for schema-qualified names, how the run ID is generated, and whether the CSV has a header row]

## Upgrading to 2.0

Since version 2.0, `--bucket` is required. The `EXPORT_BUCKET` environment variable is no longer read, so scripts and jobs that relied on it must pass `--bucket` instead. [TODO: how to check which version you have]

## Contact

[TODO: team channel or alias for questions and bug reports]