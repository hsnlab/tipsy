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
sudo virtualenv lagopus-ryu-python
source lagopus-ryu-python/bin/activate

url=https://github.com/lagopus/ryu-lagopus-ext.git
target=/opt/ryu-lagopus-ext
git clone --depth=1 $url $target
cd $target
sudo python ./setup.py install
sudo pip install msgpack-python

deactivate
