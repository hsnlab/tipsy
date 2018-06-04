#!/usr/bin/env bash

set -e

export DEBIAN_FRONTEND="noninteractive"

apt-get update
apt-get install --yes \
    ssh \
    screen \
    make \
    g++-5 \
    clang-5.0 \
    libssl-dev \
    libcap-ng-dev \
    python \
    python-dev \
    python-pip \
    python-requests \
    python-scapy \
    python-six \
    linux-headers-`uname -r` \
    git \
    autoconf \
    automake \
    libtool \
    python-pyftpdlib \
    wget \
    netcat \
    curl \
    python-tftpy \
    libnuma-dev

echo 'vm.nr_hugepages=512' > /etc/sysctl.d/hugepages.conf
sysctl -p /etc/sysctl.d/hugepages.conf

cat /vagrant/ssh/id_rsa.pub >> /home/vagrant/.ssh/authorized_keys

cd /usr/src/
wget http://fast.dpdk.org/rel/dpdk-17.11.1.tar.xz
tar xf dpdk-17.11.1.tar.xz
export DPDK_DIR=/usr/src/dpdk-stable-17.11.1
cd $DPDK_DIR

export DPDK_TARGET=x86_64-native-linuxapp-gcc
export DPDK_BUILD=$DPDK_DIR/$DPDK_TARGET
make install T=$DPDK_TARGET DESTDIR=install

git clone --branch v2.9.2 --depth=1 \
    https://github.com/openvswitch/ovs.git /opt/ovs
cd /opt/ovs
./boot.sh
./configure --with-dpdk=$DPDK_BUILD
make -j 2
make install

mkdir -p /usr/local/etc/openvswitch
mkdir -p /usr/local/var/run/openvswitch

chmod a+w /opt/tipsy/ryu # FIXME

# install ryu
git clone --depth=1 https://github.com/osrg/ryu.git /opt/ryu
cd /opt/ryu
cp /opt/tipsy/ryu/color_log.py ryu/contrib
pip install .

