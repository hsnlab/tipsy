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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import logging
import os
import subprocess
import socket
import time
from subprocess import Popen, PIPE, STDOUT

id2name = {}
log = logging.getLogger(__name__)

def call(cmd):
  log.warn(cmd)
  p = Popen(['nc', 'localhost', '16632'], stdout=PIPE, stdin=PIPE, stderr=STDOUT)
  r = p.communicate(input=b'%s\n' % cmd)[0]
  # if r.decode() != "OK":
  #   pass
  ##p.terminate()

def add_bridge(br_number, **kw):
  call('add-switch dpid=%s' % br_number)

def del_bridge(br_num, **kw):
  call('remove-switch dpid=%s' %  br_num)

def set_controller(br_num, target, **kw):
  ryu_address  = '127.0.0.1'
  ryu_ctrl     = 6633
  sw_address   = target
  sw_ctrl      =  16632 + br_num
  args = ["socat", "TCP:{}:{}".format(ryu_address, ryu_ctrl), "TCP:{}:{}".format(sw_address, sw_ctrl)]

  subprocess.Popen(args)

def add_port(dpid, portid, port_name, cores, **options):
  core_num = len(cores)
  call('add-port dpid=%s port-num=%s PCI:%s rx-queues=%s' % (
    dpid, portid, port_name, core_num))
  for que, core in enumerate(cores, start=0):
    call('lcore %s PCI:%s/%s' % (core, port_name, que))
  # call('lcore 1 PCI:%s' %  port_name)
    

def set_arp(bridge, ip, mac):
  cmd=['sudo', 'ovs-appctl', 'tnl/arp/set', bridge, ip, mac]
  if subprocess.call(cmd, stdout=open(os.devnull, 'w')):
    log.error('ovs-appctl failed (%s)' % ' '.join(cmd))
