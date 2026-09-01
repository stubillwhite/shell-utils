#!/usr/bin/env python3
import sys

import cc_data_sets

BUCKET = "s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/xocs"

DATASETS = {
    "ANI core": f"{BUCKET}/ANI/Core/output/three_column/",
    "ANI dummy": f"{BUCKET}/ANI/Dummy/output/three_column/",
    "ANI non-scopus": f"{BUCKET}/ANI/Non-scopus-publication/output/three_column/",
    "ANI preprint": f"{BUCKET}/ANI/Preprint/output/three_column/",
    "APR": f"{BUCKET}/APR/output/three_column/",
    "IHR": f"{BUCKET}/IHR/output/three_column/",
    "IPR": f"{BUCKET}/IPR/output/three_column/",
}

LABELS = {name: name for name in DATASETS}


def main():
    title = "RDP: Two column non-enriched Parquet"
    limit = cc_data_sets.date_limit(sys.argv[1:])
    prefixes_by_dataset = {
        name: cc_data_sets.list_prefixes(path) for name, path in DATASETS.items()
    }
    print(cc_data_sets.render(prefixes_by_dataset, LABELS, DATASETS, title=title, limit=limit))


if __name__ == "__main__":
    sys.exit(main())
