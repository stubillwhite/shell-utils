#!/bin/bash

# Spark shell
echo 'Installing Spark'
pushd spark-shell/ > /dev/null|| exit 1
make deps
popd > /dev/null || exit 1
