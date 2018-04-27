#!/bin/bash

gen_conf=../../lib/gen_conf.py
gen_pcap=../../lib/gen_pcap.py
classbench=../../../classbench-ng/classbench
tracegenerator=../../../trace_generator/trace_generator

pipelines=$(ls -1 $(dirname $gen_pcap)/../schema/pipeline-*.json | \
                sed -e 's/.*-\(.*\).json$/\1/g')

for pl in $pipelines; do
    echo $pl
    e=""
    e2=""
    if [ $pl == fw ]; then
        e="$e --classbench=$classbench"
        e2="$e2 --trace-generator-cmd=$tracegenerator"
        #e2="$e2 --trace-generator-pareto-a=1"
        #e2="$e2 --trace-generator-pareto-b=1"
    fi
    $gen_conf -p $pl $e -o pipeline-$pl.json
    $gen_pcap $e2 -d uplink -c pipeline-$pl.json -o t-$pl-u.pcap
    $gen_pcap $e2 -d downlink -c pipeline-$pl.json -o t-$pl-d.pcap
    time $gen_pcap $e2 -t 0 -n 10000 -c pipeline-$pl.json -o t-$pl.pcap
done
