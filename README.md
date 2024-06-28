# shell-utils #

A dumping ground for shell utilities and scripts that I use on a daily basis.

## run-spark-shell ##

- Download a local version of Spark without Hadoop and untar it
    - `wget https://archive.apache.org/dist/spark/spark-3.5.1/spark-3.5.1-bin-without-hadoop.tgz`
    - `tar xf spark-3.5.1-bin-without-hadoop.tgz`

- Download a local version of Hadoop and untar it
    - `wget https://archive.apache.org/dist/hadoop/core/hadoop-3.3.6/hadoop-3.3.6.tar.gz`
    - `tar xf hadoop-3.3.6.tar.gz`

- Create a `$SPARK_HOME/conf/spark-env.sh` in file with `SPARK_DIST_CLASSPATH` exported in it (adjusted for your path):
    - `vim spark-3.5.1-bin-without-hadoop/conf/spark-env.sh`
    - Insert the text below
        - `export SPARK_DIST_CLASSPATH=$(~/Dev/tools/dist/hadoop-3.3.6/bin/hadoop classpath)`

- Save the attached scripts (`run-spark-shell` and `run-spark-shell.scala`) in a directory on your path and make run-spark-shell executable

You should now be able to run queries against our S3 data by:

- Exporting your AWS environment variables (I run the command `aws-recs-dev`, you may have something else set up)
- `run-spark-shell`
- `spark.read.parquet("s3a://com-elsevier-recs-staging-experiments/stuw-data-quality-spike-hackery/").show`

(You have to use the S3A protocol, not S3, but otherwise paths/etc are the same. If you want to look at Live, just use the command to set the AWs environment variables to Live variables.)
 
I find this super useful for running ad hoc queries against our data sets when looking into support issues or poking around our data, and so on. Yes, you could just download the files and run locally, but that's a chore. Yes, you could do some of this with the AWS S3 query tool, but only if the parquet files are below a certain size, and some operations aren't available there. So I think it's useful, but YMMV.
 
(Usual caveat -- do this for read-only poking around, no writing to our S3 please.)
 
Happy to help you get it set up if you want, but I'm hoping the instructions capture what I did. Improvements/tweaks/etc appreciated.
