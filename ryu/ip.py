# Copyright (C) 2017 Felician Nemeth, nemethf@tmit.bme.hu
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import subprocess
import logging

log = logging.getLogger()

def call(*cmd):
  cmd = ['sudo', 'ip'] + list(cmd)
  if subprocess.call(cmd):
    log.error('ip command failed (%s)' % ' '.join(cmd))

def add_route(iface, net):
  call('route', 'add', net, 'dev', iface)

def add_route_gw(net, gw):
  call('route', 'add', net, 'via', gw)

def add_veth(name1, name2):
  call('link', 'add', 'name', name1, 'type', 'veth', 'peer', 'name', name2)

def del_veth(name1):
  call('link', 'del', 'dev', name1)

def set_up(iface, addr=None):
  call('link', 'set', 'dev', iface, 'up')
  if addr:
    call('addr', 'add', addr, 'dev', iface)

def add_arp(iface, ip_addr, hw_addr):
  cmd = ['sudo', 'arp', '-i', iface, '-s', ip_addr, hw_addr]
  if subprocess.call(cmd):
    log.error('ip command failed (%s)' % ' '.join(cmd))
