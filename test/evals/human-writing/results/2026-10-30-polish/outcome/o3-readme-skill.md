# export

`export.py` copies rows from a Postgres table to S3 as gzipped CSV files. It's an internal tool for the data team.

## Requirements

- Python 3.11 or later
- psycopg 3
- boto3

[TODO: install steps: where to get the code, and whether there is a requirements file or package to install]

## Configuration

The tool reads the database connection from the `DATABASE_URL` environment variable.

AWS credentials come from boto3's default credential chain. See the [boto3 credentials guide](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html) for where boto3 looks.

### Permissions

[TODO: the database privileges and the S3 permissions the tool needs, and whether the bucket must already exist]

## Quick start

```bash
export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DBNAME'
python export.py orders --bucket YOUR_BUCKET --dry-run
```

Replace the connection details and `YOUR_BUCKET` with your own. `--dry-run` prints what would be exported and exits.

[TODO: sample `--dry-run` output, and whether a dry run connects to the database, S3 or both]

Drop `--dry-run` to run the export.

## Usage

```text
python export.py TABLE --bucket BUCKET [--batch-size N] [--since DATE] [--dry-run]
```

| Argument | Required | Default | Meaning |
|---|---|---|---|
| `TABLE` | yes | — | Table to export. It can be schema-qualified, for example `analytics.orders`. |
| `--bucket` | yes | — | Target S3 bucket. |
| `--batch-size` | no | `5000` | Rows per batch. Each batch becomes one S3 object. |
| `--since` | no | — | Export only rows whose `updated_at` is on or after this ISO date, for example `2026-10-01`. The filter uses the table's `updated_at` column. [TODO: what happens if the table has no `updated_at` column] |
| `--dry-run` | no | off | Print what would be exported and exit. |

Export rows changed on or after 1 October 2026, in batches of 10,000:

```bash
python export.py analytics.orders --bucket YOUR_BUCKET --since 2026-10-01 --batch-size 10000
```

## Output

The tool writes one object per batch, with keys in this form:

```text
TABLE/RUN_ID/N.csv.gz
```

- `TABLE` is the table name you passed. [TODO: whether a schema-qualified name such as `analytics.orders` keeps the schema in the key]
- `RUN_ID` identifies one run of the tool. [TODO: how the run ID is generated]
- `N` is the batch number. [TODO: whether numbering starts at 0 or 1]

[TODO: CSV format: header row, column order, delimiter, quoting, how NULLs and timestamps are written]

[TODO: what a failed run leaves in S3, and whether it is safe to re-run]

## Upgrading from 1.x

Read this only if you have scripts or jobs written for a 1.x version. [TODO: how to check which version you have]

Version 2.0 made `--bucket` required. The tool no longer reads the `EXPORT_BUCKET` environment variable, so any script or job that set `EXPORT_BUCKET` must pass the bucket on the command line instead:

```bash
python export.py orders --bucket YOUR_BUCKET
```

## Support

[TODO: owner or channel to contact]