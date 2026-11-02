# parquet_lookup #

Build a local DuckDB index for Parquet datasets in S3 or on disk, then retrieve XML content by key without scanning the
datasets. AWS authentication uses the standard credential resolution supported by PyArrow.

## Build or update the index ##

```shell
make run-build-index ARGS='\
  --dataset archive \
  --path s3://my-bucket/archive/part-a/ \
  --path s3://my-bucket/archive/part-b/ \
  --key-column id \
  --content-column xml \
  --db key_index.duckdb \
  --region eu-west-1 \
  --workers 16'
```

Each build replaces that dataset’s entries across all paths in one transaction; other datasets remain in the index.

### Data versions ###

The MHub and RDP versions used by every helper script (`build-index`, `download-example-data`, `common-keys`,
`build-file-list`, `check-for-new-content`) and by the `core-complete-compare` shell function are defined in one
file, `data-versions`:

```bash
MHUB_VERSION=20260825
RDP_VERSION=20261016-094451
MHUB_OPENALEX_VERSION=20260629-073848
RDP_OPENALEX_VERSION=20261005-200000
RDP_EKB_VERSION=20261016-094451
```

To move every tool to a new data drop, edit these values and run `./build-index`. The full value selects the S3
prefix, while its first eight characters (the date) determine dataset and database names, for example
`rdp-ani-20261016.duckdb`. RDP ANI Core, Non-scopus-publication and Preprint all use `RDP_VERSION`.
`MHUB_OPENALEX_VERSION` is `<date>-<time>` and selects the single file
`s3://mhub-d2m-export-parquet-prod/ANI/<date>/Ani_Eid_XML_<date>_<time>.parquet`.

### Index presets ###

`./build-index` with no arguments builds every preset; pass one or more preset names to build only those:

```bash
./build-index
./build-index rdp_ani rdp_apr
```

| Preset              | Version                 | Columns       | Region      |
|---------------------|-------------------------|---------------|-------------|
| `mhub_ani`          | `MHUB_VERSION`          | `key` / `xml` | `us-east-1` |
| `rdp_ani`           | `RDP_VERSION`           | `id` / `xml`  | `us-east-2` |
| `mhub_apr`          | `MHUB_VERSION`          | `key` / `xml` | `us-east-1` |
| `rdp_apr`           | `RDP_VERSION`           | `id` / `xml`  | `us-east-2` |
| `mhub_openalex_ani` | `MHUB_OPENALEX_VERSION` | `EID` / `XML` | `us-east-1` |
| `rdp_openalex_ani`  | `RDP_OPENALEX_VERSION`  | `id` / `xml`  | `us-east-2` |
| `rdp_ekb_ani`       | `RDP_EKB_VERSION`       | `id` / `xml`  | `us-east-2` |

Databases are stored beside the script; if a selected database already exists, the wrapper skips that build. It
indexes S3 data directly rather than downloading all Parquet files locally.

## Look up a key ##

Search all indexed datasets:

```shell
make run-lookup ARGS='--db key_index.duckdb KEY123'
```

Restrict to one or more datasets:

```shell
make run-lookup ARGS='--db key_index.duckdb --dataset archive KEY123'
```

Successful look ups write only the XML document to `stdout`. Missing or ambiguous keys produce an error on stderr and exit
non-zero; if a key exists in multiple datasets, specify `--dataset` to select one. A key repeated within the selected
dataset is also ambiguous.

Write the result to `<out-dir>/<dataset>.xml` instead of stdout using `--out-dir`:

```shell
make run-lookup ARGS='--db key_index.duckdb --out-dir ./out KEY123'
```

## Count indexed keys ##

Print the number of indexed keys in each top-level `.duckdb` file in this directory:

```shell
make run-key-counts
```

## Download example documents ##

Run `./download-example-data` from this directory after building the selected indexes. It creates
`example-data/<dataset>/` directories for ANI, APR, and OpenAlex ANI, downloads and formats the configured XML
records, and skips existing nonempty files. Dataset names come from `data-versions` and use only the date, even
when the indexed S3 prefix contains a full timestamp (for example, `rdp-apr-20261016` for `20261016-094451`).
