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

### ANI and APR presets ###

The `build-index` wrapper provides MHub and RDP presets for ANI and APR. Uncomment exactly one `BUILD` selection
near the top of the script, then run `./build-index`. For APR, choose either:

```bash
BUILD=(mhub_apr 20260825)
```

or:

```bash
BUILD=(rdp_apr 20261005-200000)
```

Each APR preset accepts one timestamp and indexes one S3 path. MHub uses `key` / `xml` columns in `us-east-1`;
RDP uses `id` / `xml` columns in `us-east-2`. The full timestamp selects the S3 prefix, while its first eight
characters determine the dataset and database names: `mhub-apr-20260825.duckdb` or `rdp-apr-20261005.duckdb`
for these examples. Databases are stored beside the script; if the selected database already exists, the wrapper
skips the build. It indexes S3 data directly rather than downloading all Parquet files locally.

For OpenAlex ANI, choose one of:

```bash
BUILD=(mhub_openalex_ani 20260629 073848)
BUILD=(rdp_openalex_ani 20261005-200000)
```

MHub accepts a date and time and indexes the single file
`s3://mhub-d2m-export-parquet-prod/ANI/20260629/Ani_Eid_XML_20260629_073848.parquet`.
RDP accepts one timestamp and indexes the directory
`s3://sccontent-prod-corecomplete-xocs-us-east-2/prod/openalex/ANI/Core/output/three_column/20261005-200000/`.
MHub uses `EID` / `XML` columns in `us-east-1`; RDP uses `id` / `xml` columns in `us-east-2`. They produce
`mhub-openalex-ani-20260629.duckdb` and `rdp-openalex-ani-20261005.duckdb`, respectively.

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

## Download example documents ##

Run `./download-example-data` from this directory after building the selected indexes. It creates
`example-data/<dataset>/` directories for ANI, APR, and OpenAlex ANI, downloads and formats the configured XML
records, and skips existing nonempty files. Dataset names use only the date, even when the indexed
S3 prefix contains a full timestamp (for example, `rdp-apr-20260827` for `20260827-063016`).
