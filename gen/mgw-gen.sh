#!/bin/bash
set -x

USER_NUM=(  1000 250 100 10 1)
SERVER_NUM=( 100  25  10  2 1)
BST_NUM=(     10   2   2  1 1)
NHOP_NUM=(    10   2   2  1 1)
FLUCTUSERS=( 100  10   5  2 0)
HANDOVERS=(   64   8   4  1 0)
PKT_SIZE=(64 )
PKT_NUM=10000
OUT_DIR="/tmp/mgw-test"
PREFIX="mgw"
MGW_GEN_ROOT="."
MGW_GEN_CONF="${MGW_GEN_ROOT}/mgw-gen-conf.py"
MGW_GEN_PCAP="${MGW_GEN_ROOT}/mgw-gen-pcap-p.py -t 10"

################################
set +x
mkdir ${OUT_DIR}
for ((i=0;i<${#USER_NUM[@]};i++)); do
    TTYPE="u${USER_NUM[$i]}_s${SERVER_NUM[$i]}_b${BST_NUM[$i]}_n${NHOP_NUM[$i]}_h${HANDOVERS[$i]}_f${FLUCTUSERS[$i]}"
    for ((j=0;j<${#PKT_SIZE[@]};j++)); do
	CONF_FILE="${OUT_DIR}/${PREFIX}_${TTYPE}.json"
	MGW_GEN_CONF_PARAMS="-u ${USER_NUM[$i]} -s ${SERVER_NUM[$i]} -b ${BST_NUM[$i]} -n ${NHOP_NUM[$i]} "
	MGW_GEN_CONF_PARAMS+="--handovers ${HANDOVERS[$i]} --fluctusers ${FLUCTUSERS[$i]}"
	${MGW_GEN_CONF} ${MGW_GEN_CONF_PARAMS} -o ${CONF_FILE}
	for dir in d u; do
	    PCAP_FILE="${OUT_DIR}/${PREFIX}_${TTYPE}_${dir}.${PKT_SIZE[$j]}bytes.pcap"
	    ${MGW_GEN_PCAP} -c ${CONF_FILE} -d ${dir} -n ${PKT_NUM} -s ${PKT_SIZE[$j]} -o ${PCAP_FILE}
	done
    done
done
