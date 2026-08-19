#!/usr/bin/env python3
import re
import sys

import cc_data_sets

DATE_PATTERN = re.compile(r"^\d{8}$")

ENRICHED = {
    "ANI": "s3://sccontent-ani-parquet-prod/",
    "APR": "s3://sccontent-apr-parquet-prod/",
    "IPR": "s3://sccontent-ipr-parquet-prod/",
    "IH": "s3://sccontent-ih-parquet-prod/",
}

PARSED = {
    "ANI core": "s3://sccontent-parsed-ani-core-parquet-prod/",
    "APR": "s3://sccontent-parsed-apr-parquet-prod/",
    "IPR": "s3://sccontent-parsed-ipr-parquet-prod/",
}

GROUPS = [
    ("Two column XOCS enriched Parquet", ENRICHED),
    ("Parsed Parquet", PARSED),
]


def keep_dates(prefixes):
    return frozenset(prefix for prefix in prefixes if DATE_PATTERN.match(prefix))


def render_group(title, paths, limit):
    prefixes_by_dataset = {
        name: keep_dates(cc_data_sets.list_prefixes(path))
        for name, path in paths.items()
    }
    labels = {name: name for name in paths}
    return cc_data_sets.render(
        prefixes_by_dataset, labels, paths, title=title, limit=limit
    )


def main():
    limit = cc_data_sets.date_limit(sys.argv[1:])
    print("\n\n".join(render_group(title, paths, limit) for title, paths in GROUPS))


if __name__ == "__main__":
    sys.exit(main())
