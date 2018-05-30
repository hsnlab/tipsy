#!/bin/sh
export DPDK_DIR=/usr/src/dpdk-stable-17.11.1
modprobe uio_pci_generic
modprobe openvswitch

$DPDK_DIR/usertools/dpdk-devbind.py --force --bind=uio_pci_generic 0000:00:08.0
$DPDK_DIR/usertools/dpdk-devbind.py --force --bind=uio_pci_generic 0000:00:09.0


export PATH=$PATH:/usr/local/share/openvswitch/scripts
export DB_SOCK=/usr/local/var/run/openvswitch/db.sock
export DB_CONF=/usr/local/etc/openvswitch/conf.db
cd /opt/ovs
ovsdb/ovsdb-tool create ${DB_CONF} vswitchd/vswitch.ovsschema
ovsdb/ovsdb-server --remote=punix:${DB_SOCK} \
    --remote=db:Open_vSwitch,Open_vSwitch,manager_options \
    --pidfile --detach
utilities/ovs-vsctl --no-wait set Open_vSwitch . other_config:dpdk-init=true
vswitchd/ovs-vswitchd unix:${DB_SOCK} --pidfile --detach
utilities/ovs-ctl --no-ovsdb-server --db-sock="$DB_SOCK" start
