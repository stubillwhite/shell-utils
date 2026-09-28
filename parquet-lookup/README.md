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
