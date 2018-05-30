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

echo 'vm.nr_hugepages=2048' > /etc/sysctl.d/hugepages.conf
#sysctl -w vm.nr_hugepages=2048

cd /usr/src/
wget http://fast.dpdk.org/rel/dpdk-17.11.1.tar.xz
tar xf dpdk-17.11.1.tar.xz
export DPDK_DIR=/usr/src/dpdk-stable-17.11.1
cd $DPDK_DIR

export DPDK_TARGET=x86_64-native-linuxapp-gcc
export DPDK_BUILD=$DPDK_DIR/$DPDK_TARGET
make install T=$DPDK_TARGET DESTDIR=install

modprobe uio_pci_generic
modprobe openvswitch
$DPDK_DIR/usertools/dpdk-devbind.py --force --bind=uio_pci_generic 0000:00:08.0
$DPDK_DIR/usertools/dpdk-devbind.py --force --bind=uio_pci_generic 0000:00:09.0

git clone https://github.com/openvswitch/ovs.git /opt/ovs
cd /opt/ovs
./boot.sh
./configure --with-dpdk=$DPDK_BUILD
make -j 2
make install

export PATH=$PATH:/usr/local/share/openvswitch/scripts
export DB_SOCK=/usr/local/var/run/openvswitch/db.sock
mkdir -p /usr/local/etc/openvswitch
mkdir -p /usr/local/var/run/openvswitch

ovsdb/ovsdb-tool create /usr/local/etc/openvswitch/conf.db \
    vswitchd/vswitch.ovsschema
ovsdb/ovsdb-server --remote=punix:/usr/local/var/run/openvswitch/db.sock \
    --remote=db:Open_vSwitch,Open_vSwitch,manager_options \
    --pidfile --detach
utilities/ovs-vsctl --no-wait set Open_vSwitch . other_config:dpdk-init=true
vswitchd/ovs-vswitchd unix:/usr/local/var/run/openvswitch/db.sock \
    --pidfile --detach
utilities/ovs-ctl --no-ovsdb-server --db-sock="$DB_SOCK" start

git clone https://github.com/hsnlab/tipsy /opt/tipsy
cd /opt/tipsy
export PATH=$PWD:$PATH
