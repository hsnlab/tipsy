#!/usr/bin/env python2

# TIPSY: Telco pIPeline benchmarking SYstem
#
# Copyright (C) 2017-2018 by its authors (See AUTHORS)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.    See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
"""
TIPSY controller for T4P4S pipeline
Run as:

   $ ./tipsy.py

"""

import requests
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time
from subprocess import Popen

conf_file = '/tmp/pipeline.json'
t4p4s_conf_l2fwd = '/tmp/l2fwd_conf.txt'
t4p4s_conf_portfwd = '/tmp/portfwd_conf.txt'
t4p4s_conf_l3fwd = '/tmp/l3fwd_conf.txt'

webhook_configured = 'http://localhost:9000/configured'

###########################################################################

def call_cmd(cmd):
    print(' '.join(cmd))
    return subprocess.call(cmd)


class ObjectView(object):
    def __init__(self, **kwargs):
        kw = {k.replace('-', '_'): v for k, v in kwargs.items()}
        self.__dict__.update(kw)

    def __repr__(self):
        return self.__dict__.__repr__()

    def get (self, attr, default=None):
        return self.__dict__.get(attr, default)


class PL(object):
    def __init__(self, parent, conf):
        self.conf = conf
        self.parent = parent
        self.cont_config = None
        self.p4_source = None
        self.p4_version = 'v14'
        self.t4p4s_home = '/home/eptevor/t4p4s16/t4p4s-16/'
	self._process = None

    def compile_and_start(self):
        cmd = './t4p4s.sh'
        p4src = os.path.join(self.t4p4s_home, self.p4_source)
        print([cmd, self.p4_version, 'ctrcfg', self.cont_config, p4src], 'cwd=', self.t4p4s_home)
        self._process = subprocess.Popen([cmd, self.p4_version, 'ctrcfg', self.cont_config, p4src], cwd = self.t4p4s_home)

    def stop(self):
        if self._process:
            self._process.terminate()


class PL_l2fwd(PL):
    """L2 Packet Forwarding

    Upstream the L2fwd pipeline will receive packets from the downlink
    port, perform a lookup for the destination MAC address in a static
    MAC table, and if a match is found the packet will be forwarded to
    the uplink port or otherwise dropped (or likewise forwarded upstream
    if the =fakedrop= parameter is set to =true=).    The downstream
    pipeline is just the other way around, but note that the upstream
    and downstream pipelines use separate MAC tables.
    """

    def __init__(self, parent, conf):
        super(PL_l2fwd, self).__init__(parent, conf)
        self.p4_source = 'examples/l2-switch-test.p4'
        self.p4_version = 'v14'
        self.cont_config = t4p4s_conf_l2fwd

    def config_switch(self):
        # Create a config file for t4p4s controller
        with open(t4p4s_conf_l2fwd, 'w') as conf_file:
            for entry in self.conf.upstream_table:
                out_port = entry.out_port or 0
                conf_file.write("%s %d\n" % (entry.mac, out_port))

            for entry in self.conf.downstream_table:
                out_port = entry.out_port or 1
                conf_file.write("%s %d\n" % (entry.mac, out_port))

class PL_portfwd(PL):
    """Port Forwarding

    TBA
    """

    def __init__(self, parent, conf):
        super(PL_portfwd, self).__init__(parent, conf)
        self.p4_source = 'examples/portfwd.p4'
        self.p4_version = 'v14'
        self.cont_config = t4p4s_conf_portfwd

    def config_switch(self):
        # Create a config file for t4p4s controller
        with open(t4p4s_conf_portfwd, 'w') as conf_file:
            if self.conf.mac_swap_downstream:
                conf_file.write("0 1 1 %s\n" % self.conf.mac_swap_downstream)
            else:
                conf_file.write("0 1 0 11:11:11:11:11:11\n")
            if self.conf.mac_swap_upstream:
                conf_file.write("1 0 1 %s\n" % self.conf.mac_swap_upstream)
            else:
                conf_file.write("1 0 0 11:11:11:11:11:11\n")

class PL_l3fwd(PL):
    """L3 Forwarding
    
    TBA
    """

    def __init__(self, parent, conf):
        super(PL_l3fwd, self).__init__(parent, conf)
        self.p4_source = 'examples/l3fwd.p4'
        self.p4_version = 'v14'
        self.cont_config = t4p4s_conf_l3fwd

    def config_switch(self):
        # Create a config file for t4p4s controller
        with open(t4p4s_conf_l3fwd, 'w') as conf_file:
            # Processing nexthop groups
            nhg_idx = 0
            for nhg in self.conf.downstream_group_table:
                conf_file.write("N %d %d %s %s\n" % (nhg_idx, nhg.port, nhg.smac, nhg.dmac))
                nhg_idx += 1

            nhg_offset = nhg_idx #len(self.conf.downstream_group_table)
            
            for nhg in self.conf.upstream_group_table:
                conf_file.write("N %d %d %s %s\n" % (nhg_idx, nhg.port, nhg.smac, nhg.dmac))
                nhg_idx += 1
            
            # Filling L3fwd tables
            for l3entry in self.conf.downstream_l3_table:
                conf_file.write("E %s %d %d\n" % (l3entry.ip, l3entry.prefix_len, l3entry.nhop))

            for l3entry in self.conf.upstream_l3_table:
                conf_file.write("E %s %d %d\n" % (l3entry.ip, l3entry.prefix_len, nhg_offset + l3entry.nhop))

            # SUT
            conf_file.write("M %s\n" % self.conf.sut.dl_port_mac)
            conf_file.write("M %s\n" % self.conf.sut.ul_port_mac)


class Tipsy(object):

    def __init__(self, *args, **kwargs):
        super(Tipsy, self).__init__(*args, **kwargs)
        Tipsy._instance = self

        self.conf_file = conf_file

        print("conf_file: %s" % self.conf_file)

        try:
            with open(self.conf_file, 'r') as f:
                conv_fn = lambda d: ObjectView(**d)
                self.pl_conf = json.load(f, object_hook=conv_fn)
        except IOError as e:
            print('Failed to load cfg file (%s): %s' % (self.conf_file, e))
            raise(e)
        except ValueError as e:
            print('Failed to parse cfg file (%s): %s' % (self.conf_file, e))
            raise(e)
        try:
            self.pl = globals()['PL_%s' % self.pl_conf.name](self, self.pl_conf)
        except (KeyError, NameError) as e:
            print('Failed to instanciate pipeline (%s): %s' %
                  (self.pl_conf.name, e))
            raise(e)

    def start_datapath(self):
        self.pl.compile_and_start()

        time.sleep(60)
        try:
            requests.get(webhook_configured)
        except requests.ConnectionError:
            pass

    def stop_datapath(self):
        self.pl.stop()

    def configure(self):
        self.pl.config_switch()

    def stop(self):
        self.stop_datapath()


def handle_sigint(sig_num, stack_frame):
    Tipsy().stop()

signal.signal(signal.SIGINT, handle_sigint)

if __name__ == "__main__":
    Tipsy().configure()
    print('DataPath configured...')
    Tipsy().start_datapath()
    print('DataPath started...')
    signal.pause()

