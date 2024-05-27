#!/bin/bash

# Generate random datafile with 5 columns of numbers from the range of
# 1-10

i=$1
echo "{ \"id\": $i }" > $PWD/result.json

for row in {1..10}; do
    echo -n $row " " >> $PWD/datafile.data
    shuf -i 1-10 -n 4 | tr "\n" " " >> $PWD/datafile.data
    echo >> $PWD/datafile.data
done

