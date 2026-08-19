#!/usr/bin/env python3
import sys

import cc_data_sets

BUCKET = "s3://sccontent-prod-corecomplete-xocs/prod/xocs"

DATASETS = {
    "ANI": f"{BUCKET}/ANI/Core/output/three_column/",
    "APR": f"{BUCKET}/APR/output/three_column/",
    "IHR": f"{BUCKET}/IHR/output/three_column/",
    "IPR": f"{BUCKET}/IPR/output/three_column/",
}


def dataset_label(name):
    segments = DATASETS[name].rstrip("/").split("/")
    extra = segments[segments.index(name) + 1 : segments.index("output")]
    return " ".join([name, *(segment.lower() for segment in extra)])


LABELS = {name: dataset_label(name) for name in DATASETS}


def main():
    limit = cc_data_sets.date_limit(sys.argv[1:])
    prefixes_by_dataset = {
        name: cc_data_sets.list_prefixes(path) for name, path in DATASETS.items()
    }
    print(cc_data_sets.render(prefixes_by_dataset, LABELS, DATASETS, limit=limit))


if __name__ == "__main__":
    sys.exit(main())
