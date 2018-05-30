#!/usr/bin/env bash

set -e

export DEBIAN_FRONTEND="noninteractive"

apt-get update
apt-get install --yes \
    build-essential \
    make \
    cmake \
    linux-headers-`uname -r` \
    pciutils libnuma-dev \
    git \
    gcc \
    ssh \
    python-pip \
    python-dev \
    python-jsonschema \
    python-matplotlib \
    libffi-dev \
    libssl-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    texlive-latex-base \
    scapy

pip install ryu

route del -net 192.168.50.0 netmask 255.255.255.0 eth1
route del -net 192.168.50.0 netmask 255.255.255.0 eth2

git clone https://github.com/emmericp/MoonGen.git /opt/MoonGen

cd /opt/MoonGen
./build.sh
./setup-hugetlbfs.sh

git clone https://github.com/hsnlab/tipsy /opt/tipsy
cd /opt/tipsy
export PATH=$PWD:$PATH
