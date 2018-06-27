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
    python3-jsonschema \
    python3-matplotlib \
    libffi-dev \
    libssl-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    texlive-latex-base \
    texlive-pictures \
    #scapy


# scapy 2.2 in "Ubuntu 16.04.4 LTS, xenial" does not support VXLAN
pip install scapy

echo 192.168.53.3 sut.local >> /etc/hosts

git clone --depth=1 https://github.com/emmericp/MoonGen.git /opt/MoonGen
cd /opt/MoonGen
./build.sh

url=https://github.com/classbench-ng/classbench-ng.git
target=/opt/classbench-ng
git clone --depth=1 $url $target
cd $target
make
gem install open4 ruby-ip docopt ipaddress

url=https://www.arl.wustl.edu/classbench/trace_generator.tar.gz
wget -qO- $url | tar xz -C /opt
cd /opt/trace_generator
make all
