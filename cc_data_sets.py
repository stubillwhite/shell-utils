import argparse
import re
import subprocess

PREFIX_PATTERN = re.compile(r"^\s+PRE\s+(\S+)/$")

DEFAULT_DATE_LIMIT = 5


def date_limit(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show all dates instead of just the most recent",
    )
    return None if parser.parse_args(argv).all else DEFAULT_DATE_LIMIT


def parse_prefixes(listing):
    return frozenset(
        match.group(1)
        for match in (PREFIX_PATTERN.match(line) for line in listing.splitlines())
        if match
    )


def list_prefixes(path):
    result = subprocess.run(
        ["aws", "s3", "ls", path],
        capture_output=True,
        text=True,
        check=True,
    )
    return parse_prefixes(result.stdout)


def build_table(prefixes_by_dataset):
    names = list(prefixes_by_dataset)
    dates = sorted(set().union(*prefixes_by_dataset.values()))
    rows = [
        (date, ["OK" if date in prefixes_by_dataset[name] else "-" for name in names])
        for date in dates
    ]
    return names, rows


def latest_complete(prefixes_by_dataset):
    common = frozenset.intersection(*prefixes_by_dataset.values())
    return max(common) if common else None


def latest_any(prefixes_by_dataset):
    everything = frozenset().union(*prefixes_by_dataset.values())
    return max(everything) if everything else None


def datasets_with(prefixes_by_dataset, prefix):
    return [
        name for name, prefixes in prefixes_by_dataset.items() if prefix in prefixes
    ]


def format_table(names, rows):
    date_width = max(len("Date"), *(len(row[0]) for row in rows)) if rows else len("Date")
    widths = [max(len(name), 2) for name in names]
    header = f"{'Date':<{date_width}}  " + "  ".join(
        f"{name:>{width}}" for name, width in zip(names, widths)
    )
    lines = [
        f"{date:<{date_width}}  "
        + "  ".join(f"{cell:>{width}}" for cell, width in zip(cells, widths))
        for date, cells in rows
    ]
    return "\n".join([header, *lines])


def format_dataset_paths(prefix, names, labels, paths):
    tags = {name: f"{labels[name]}:" for name in names}
    width = max(len(tag) for tag in tags.values())
    return "\n".join(
        f"{tags[name]:<{width}} {paths[name]}{prefix}/" for name in names
    )


def render(prefixes_by_dataset, labels, paths, title=None, limit=None):
    names, rows = build_table(prefixes_by_dataset)
    if limit is not None:
        rows = rows[-limit:]
    blocks = [format_table(names, rows)]

    complete = latest_complete(prefixes_by_dataset)
    if complete is not None:
        blocks.append(
            f"Latest complete data set: {complete}\n"
            + format_dataset_paths(complete, names, labels, paths)
        )

    latest = latest_any(prefixes_by_dataset)
    if latest is not None:
        blocks.append(
            f"Latest data sets: {latest}\n"
            + format_dataset_paths(
                latest, datasets_with(prefixes_by_dataset, latest), labels, paths
            )
        )

    heading = [f"== {title} =="] if title is not None else []
    return "\n\n".join([*heading, *blocks])
