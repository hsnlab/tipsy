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

def del_veth(name1, _name2=None):
  call('link', 'del', 'dev', name1)

def set_up(iface, addr=None):
  call('link', 'set', 'dev', iface, 'up')
  if addr:
    call('addr', 'add', addr, 'dev', iface)

def add_arp(iface, ip_addr, hw_addr):
  cmd = ['sudo', 'arp', '-i', iface, '-s', ip_addr, hw_addr]
  if subprocess.call(cmd):
    log.error('ip command failed (%s)' % ' '.join(cmd))
