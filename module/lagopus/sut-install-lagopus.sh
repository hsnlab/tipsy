#!/bin/sh

# https://github.com/lagopus/lagopus/blob/master/QUICKSTART.md

sudo apt-get install \
        build-essential \
        libgmp-dev \
        libssl-dev \
        libpcap-dev \
        libnuma-dev \
        byacc \
        flex \
        git \
	python-dev \
        linux-headers-$(uname -r) \
        librte-acl2


url=https://github.com/lagopus/lagopus.git
target=/opt/lagopus
git clone --depth=1 $url $target
cd $target

./configure
make -j `nproc`
sudo make install

cd /opt
sudo apt-get install \
     python-virtualenv
sudo virtualenv lagpus-virtualenv
source lagpus-virtualenv/bin/activate

url=https://github.com/lagopus/ryu-lagopus-ext.git
target=/opt/ryu-lagopus-ext
branch=lagopus-general-tunnel-ext
git clone --branch $branch --depth=1 $url $target
cd $target

cp ../tipsy/module/openflow/color_log.py ryu/contrib
pip install .
#sudo pip install msgpack-python

deactivate
