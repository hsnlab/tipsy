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
    scapy

echo 192.168.53.3 sut.local >> /etc/hosts

git clone --depth=1 https://github.com/emmericp/MoonGen.git /opt/MoonGen
cd /opt/MoonGen
./build.sh

cd /opt/tipsy
export PATH=$PWD:$PATH
