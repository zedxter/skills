"""csvdiff: compare two CSV files by a key column and print added, removed and changed rows."""
import argparse
import os
import sys


def main(argv=None):
    p = argparse.ArgumentParser(prog="csvdiff", description="Compare two CSV files by a key column.")
    p.add_argument("old", help="the older CSV file")
    p.add_argument("new", help="the newer CSV file")
    p.add_argument("-k", "--key", default="id", help="column that identifies a row (default: id)")
    p.add_argument("--ignore", action="append", default=[], help="column to ignore when comparing; repeatable")
    p.add_argument("-f", "--format", choices=["text", "json"], default="text", help="output format (default: text)")
    p.add_argument("--delimiter", default=",", help="field delimiter (default: ,)")
    args = p.parse_args(argv)
    encoding = os.environ.get("CSVDIFF_ENCODING", "utf-8")
    # exit codes: 0 no differences, 1 differences found, 2 bad input
    ...


if __name__ == "__main__":
    sys.exit(main())
