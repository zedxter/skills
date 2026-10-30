"""export tool: copies rows from Postgres to S3 as gzipped CSV."""
import argparse

def main():
    p = argparse.ArgumentParser(description="Export a Postgres table to S3 as gzipped CSV.")
    p.add_argument("table", help="table name, optionally schema-qualified")
    p.add_argument("--bucket", required=True, help="target S3 bucket")
    p.add_argument("--batch-size", type=int, default=5000, help="rows per batch (default 5000)")
    p.add_argument("--since", help="only rows with updated_at on or after this ISO date")
    p.add_argument("--dry-run", action="store_true", help="print what would be exported and exit")
    return p.parse_args()

if __name__ == "__main__":
    main()
