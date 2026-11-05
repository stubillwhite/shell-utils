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

## Collect samples for upload ##

```shell
./get-samples-to-upload ani 0013885228
./get-samples-to-upload openalex-ani ./ids.txt
```

Pass a single ID or an existing file containing one ID per line. Blank lines are ignored; Windows line endings
and a final line without a newline are supported. IDs must not contain `/`.

The script ensures the exact ID's XML is cached in `./example-data/<source>/<ID>.xml` for the current MHub/RDP
source pair: ANI for `ani`, APR for `apr`, or OpenAlex ANI for `openalex-ani`. Source versions come from
`data-versions`. Missing or empty files are downloaded even if older versions or ID-prefix matches exist.
Nonempty cached files are reused.

Downloads require existing source indexes (build the relevant presets with `./build-index` first), the lookup
dependencies, `xmllint`, zsh, and the AWS CLI on PATH. Configure AWS credentials before running the script.
When downloads are needed, it validates existing credentials once with `aws sts get-caller-identity`.
If validation fails, it reports an error and exits before downloading or changing upload contents.
The script does not log in or load shell configuration. Fully cached requests do not check AWS credentials.

The script clears `./upload/*.xml` once, collects nonempty local ID-prefix matches from all matching dated
folders, and opens `./upload` once. This includes additional local sources and newly downloaded records.
Download/query/XML failures are reported while remaining records continue; available samples are still collected
and opened, but the command exits nonzero. Empty input lists perform no downloads.
