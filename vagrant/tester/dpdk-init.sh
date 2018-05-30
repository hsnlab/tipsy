#!/bin/sh
ip link set dev eth1 down
ip link set dev eth2 down
/opt/MoonGen/setup-hugetlbfs.sh
/opt/MoonGen/bind-interfaces.sh
