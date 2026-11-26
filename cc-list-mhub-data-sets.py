#!/usr/bin/env python3
import re
import sys

import cc_data_sets

DATE_PATTERN = re.compile(r"^\d{8}$")

USE_MHUB_SNAPSHOT_LOCATION = True

SNAPSHOT_ROOT = "s3://sccontent-staging-corecomplete-xocs/"

ENRICHED_BUCKETS = {
    "ANI": "sccontent-ani-parquet-prod",
    "ANI dummy": "sccontent-anidummy-parquet-prod",
    "APR": "sccontent-apr-parquet-prod",
    "IPR": "sccontent-ipr-parquet-prod",
    "IH": "sccontent-ih-parquet-prod",
}


def enriched_paths(use_snapshot_location):
    root = SNAPSHOT_ROOT if use_snapshot_location else "s3://"
    return {name: f"{root}{bucket}/" for name, bucket in ENRICHED_BUCKETS.items()}


ENRICHED = enriched_paths(use_snapshot_location=USE_MHUB_SNAPSHOT_LOCATION)

PARSED = {
    "ANI core": "s3://sccontent-parsed-ani-core-parquet-prod/",
    "ANI dummy": "s3://sccontent-parsed-ani-dummy-parquet-prod/",
    "APR": "s3://sccontent-parsed-apr-parquet-prod/",
    "IPR": "s3://sccontent-parsed-ipr-parquet-prod/",
}

GROUPS = [
    ("MHub/XFAB: Two column XOCS enriched Parquet", ENRICHED),
    #  ("MHub/XFAB: Parsed Parquet", PARSED),
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
