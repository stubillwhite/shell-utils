# Constants
# ==============================================================================

COLOR_BLUE=\033[0;34m
COLOR_NONE=\033[0m

SPARK_ZIP_URL=https://archive.apache.org/dist/spark/spark-3.5.1/spark-3.5.1-bin-without-hadoop.tgz
HADOOP_ZIP_URL=https://archive.apache.org/dist/hadoop/core/hadoop-3.3.6/hadoop-3.3.6.tar.gz

SPARK_ZIP=$(shell echo $(SPARK_ZIP_URL) | gsed 's|.*/\([^/]\+\)|\1|g')
HADOOP_ZIP=$(shell echo $(HADOOP_ZIP_URL) | gsed 's|.*/\([^/]\+\)|\1|g')

SPARK_DIR=$(shell echo $(SPARK_ZIP) | gsed 's/\(.tgz\|.tar.gz\)//g')
HADOOP_DIR=$(shell echo $(HADOOP_ZIP) | gsed 's/\(.tgz\|.tar.gz\)//g')
SPARK_ENV_FILE=$(SPARK_DIR)/conf/spark-env.sh

SENTINEL_FILE_SPARK=.make-sentinel.spark
SENTINEL_FILE_HADOOP=.make-sentinel.hadoop
SENTINEL_FILE_SPARK_ENV=.make-sentinel.spark-env

SPARK_HOME=$(basename $(SPARK_DIR))

# Targets
# ==============================================================================

# Help
# ======================================

help:
	@grep -E '^[0-9a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "$(COLOR_BLUE)%s|$(COLOR_NONE)%s\n", $$1, $$2}' \
		| column -t -s '|'

# Clean
# ======================================

.PHONY: clean
clean: ## Remove all artefacts
	@echo 'Cleaning application'

.PHONY: clean-all
clean-all: clean ## Remove all artefacts and dependencies
	@echo 'Cleaning dependencies'
	@rm -rf $(SPARK_ZIP) $(HADOOP_ZIP)

# Dependencies
# ======================================

$(SENTINEL_FILE_SPARK):
	@echo 'Fetching Spark'
	@wget $(SPARK_ZIP_URL)
	@tar xzvf $(SPARK_ZIP)
	@touch $(SENTINEL_FILE_SPARK)

$(SENTINEL_FILE_HADOOP):
	@echo 'Fetching Hadoop'
	@wget $(HADOOP_ZIP_URL)
	@tar xzvf $(HADOOP_ZIP)
	@touch $(SENTINEL_FILE_HADOOP)

$(SENTINEL_FILE_SPARK_ENV): $(SENTINEL_FILE_SPARK) $(SENTINEL_FILE_HADOOP)
	@echo 'export SPARK_DIST_CLASSPATH=$$('$(abspath $(HADOOP_DIR))/bin/hadoop 'classpath)' > $(SPARK_ENV_FILE)
	@touch $(SENTINEL_FILE_SPARK_ENV)

spark: $(SENTINEL_FILE_SPARK_ENV)

deps: spark ## Install dependencies

