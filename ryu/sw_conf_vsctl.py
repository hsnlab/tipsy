# TIPSY: Telco pIPeline benchmarking SYstem
#
# Copyright (C) 2017-2018 by it's authors (See AUTHORS)
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

id2name = {}
log = logging.getLogger(__name__)

def del_old_ports(dp_id):
  bridge = get_bridge_by_datapath_id(dp_id)
  for port in list_ports(bridge):
    if port.find('bst-') >= 0:
      del_port(dp_id, port)

def check_output(*cmd):
  cmd = ['sudo', 'ovs-vsctl'] + list(cmd)
  try:
    res = subprocess.check_output(cmd)
    return res
  except subprocess.CalledProcessError:
    log.error('ovs-vsctl failed (%s)' % ' '.join(cmd))
    return ""

def call(*cmd):
  cmd = ['sudo', 'ovs-vsctl'] + list(cmd)
  if subprocess.call(cmd):
    log.error('ovs-vsctl failed (%s)' % ' '.join(cmd))

def list_ports(bridge):
  res = check_output('list-ports', bridge)
  return res.strip().split('\n')

def list_bridges():
  res = check_output('list-br')
  return res.strip().split('\n')

def add_bridge(br_name, **kw):
  cmd = ['add-br', br_name]
  if kw:
    cmd += ['--', 'set', 'bridge', br_name]
    for key, value in kw.items():
      key = key.replace('_', '-')
      cmd.append('other_config:%s=%s' % (key, value))

  call(*cmd)

def del_bridge(br_name, can_fail=True, **kw):
  cmd = ['del-br', br_name]
  if not can_fail:
    cmd = ['--if-exists'] + cmd
  call(*cmd)

def set_controller(br_name, target, **kw):
  call('set-controller', br_name, target)

def _get_bridge_by_datapath_id(dp_id0):
  global id2name
  for br in list_bridges():
    dp_id = check_output('get', 'bridge', br, 'datapath_id')
    dp_id = int(dp_id.replace('"', ''), 16)
    log.info('dp_id(%s) is bridge(%s)' % (dp_id, br))
    id2name[dp_id] = br
  return id2name.get(dp_id0)

def get_bridge_by_datapath_id(dp_id):
  global id2name
  if not id2name.get(dp_id):
    id2name[dp_id] = _get_bridge_by_datapath_id(dp_id)
  return id2name.get(dp_id)

def del_port(dp_id, name):
  "If dp_id is omitted, port is removed from whatever bridge contains it"
  cmd = ['del-port']
  if dp_id:
    bridge = get_bridge_by_datapath_id(dp_id)
    cmd.append(bridge)
  cmd.append(name)
  log.debug('Deleting port: %s' % name)
  call(*cmd)

def add_port_by_name(brName, portName, **kw):
  "Add port and interfacer to an OVS bridge"
  cmd = ['add-port', brName, portName]
  if kw:
    cmd += ['--', 'set', 'Interface', portName]
    for col, val in kw.items():
      if type(val) == str:
        cmd.append('%s=%s' % (col, val))
      elif type(val) == dict:
        for key, value in val.items():
          cmd.append('%s:%s=%s' % (col, key, value))
      else:
        log.error('add_port: unknown type (%s)' % type(val))
  call(*cmd)

def add_port(dpId_or_brName, portName, **options):
  if type(dpId_or_brName) == int:
    brName = get_bridge_by_datapath_id(dpId_or_brName)
  else:
    brName = dpId_or_brName
  add_port_by_name(brName, portName, **options)

def set_datapath_type(bridge, type):
  call('set', 'bridge', bridge, 'datapath_type=%s' % type)

def set_fail_mode(bridge, mode):
  "mode: [standalone, secure]"
  call('set-fail-mode', bridge, mode)

def set_arp(bridge, ip, mac):
  cmd=['sudo', 'ovs-appctl', 'tnl/arp/set', bridge, ip, mac]
  if subprocess.call(cmd, stdout=open(os.devnull, 'w')):
    log.error('ovs-appctl failed (%s)' % ' '.join(cmd))
