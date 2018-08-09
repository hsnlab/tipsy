# TIPSY: Telco pIPeline benchmarking SYstem
#
# Copyright (C) 2018 by its authors (See AUTHORS)
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

import os
import re
import subprocess
import sys

from ryu.lib import hub

sys.path.append(os.path.dirname(__file__))
import ip
import sw_conf_vsctl as sw_conf

fdir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(fdir, '..', '..', 'lib'))
import find_mod

RyuAppOpenflow = find_mod.find_class('RyuApp', 'openflow')

class RyuApp(RyuAppOpenflow):
  def __init__(self, *args, **kwargs):
    if 'switch_type' not in kwargs:
      kwargs['switch_type'] = ['ovs', 'openflow']
    super(RyuApp, self).__init__(*args, **kwargs)

  def add_port(self, br_name, port_name, iface, core=1):
    """Add a new port to an ovs bridge.
    iface can be a PCI address (type => dpdk), or
    a kernel interface name (type => system)
    """
    opt = {}
    if core != 1:
      opt['n_rxq'] = core
    # We could be smarter here, but this will do
    if iface.find(':') > 0:
      opt['dpdk-devargs'] = iface
      sw_conf.add_port(br_name, port_name, type='dpdk', options=opt)
    else:
      sw_conf.add_port(br_name, port_name, type='system', name=iface)

  def add_vxlan_tun (self, prefix, host):
      sw_conf.add_port(self.dp_id,
                       prefix + '-%s' % host.id,
                       type='vxlan',
                       options={'key': 'flow',
                                'remote_ip': host.ip})

  def initialize_dp_simple(self):
    # datapath without tunnels
    sw_conf.del_bridge('br-phy', can_fail=False)
    sw_conf.set_coremask(self.bm_conf.sut.coremask)
    br_name = 'br-main'
    sw_conf.del_bridge(br_name, can_fail=False)
    sw_conf.add_bridge(br_name, dp_desc=br_name)
    sw_conf.set_datapath_type(br_name, 'netdev')
    sw_conf.set_controller(br_name, 'tcp:127.0.0.1')
    sw_conf.set_fail_mode(br_name, 'secure')
    core = self.bm_conf.pipeline.core
    self.add_port(br_name, 'ul_port', self.ul_port_name, core=core)
    self.add_port(br_name, 'dl_port', self.dl_port_name, core=core)

  def stop_dp_simple(self):
    sw_conf.del_bridge('br-main')

  def initialize_dp_tunneled(self):
    sw_conf.set_coremask(self.bm_conf.sut.coremask)
    core = self.bm_conf.pipeline.core
    br_name = 'br-main'
    sw_conf.del_bridge(br_name, can_fail=False)
    sw_conf.add_bridge(br_name, dp_desc=br_name)
    sw_conf.set_datapath_type(br_name, 'netdev')
    sw_conf.set_controller(br_name, 'tcp:127.0.0.1')
    sw_conf.set_fail_mode(br_name, 'secure')
    self.add_port(br_name, 'ul_port', self.ul_port_name, core=core)

    br_name = 'br-phy'
    sw_conf.del_bridge(br_name, can_fail=False)
    sw_conf.add_bridge(br_name, hwaddr=self.pl_conf.gw.mac, dp_desc=br_name)
    sw_conf.set_datapath_type(br_name, 'netdev')
    self.add_port(br_name, 'dl_port', self.dl_port_name, core=core)
    ip.set_up(br_name, self.pl_conf.gw.ip + '/24')

    ip.add_veth('veth-phy', 'veth-main')
    ip.set_up('veth-main')
    ip.set_up('veth-phy')
    sw_conf.add_port('br-main', 'veth-main', type='system')
    sw_conf.add_port('br-phy', 'veth-phy', type='system')
    # Don't use a controller for the following static rules
    cmd = 'sudo ovs-ofctl --protocol OpenFlow13 add-flow br-phy priority=1,'
    in_out = [('veth-phy', 'dl_port'),
              ('dl_port', 'br-phy'),
              ('br-phy', 'dl_port')]
    for in_port, out_port in in_out:
      cmd_tail = 'in_port=%s,actions=output:%s' % (in_port, out_port)
      if subprocess.call(cmd + cmd_tail, shell=True):
        self.logger.error('cmd failed: %s' % cmd)

    nets = {}
    for host in self.pl.get_tunnel_endpoints():
      net = re.sub(r'[.][0-9]+$', '.0/24', host.ip)
      nets[str(net)] = True
    for net in nets.iterkeys():
      ip.add_route_gw(net, self.pl_conf.gw.default_gw.ip)
    self.set_arp_table()

  def stop_dp_tunneled(self):
    sw_conf.del_bridge('br-main')
    sw_conf.del_bridge('br-phy')
    ip.del_veth('veth-phy', 'veth-main')

  def initialize_datapath(self):
    self.change_status('initialize_datapath')

    if self.pl.has_tunnels:
      self.initialize_dp_tunneled()
    else:
      self.initialize_dp_simple()

  def stop_datapath(self):
    if self.pl.has_tunnels:
      self.stop_dp_tunneled()
    else:
      self.stop_dp_simple()

  def set_arp_table(self):
    def_gw = self.pl_conf.gw.default_gw
    sw_conf.set_arp('br-phy', def_gw.ip, def_gw.mac)
    self.logger.debug('br-phy: Update the ARP table')
    hub.spawn_after(60 * 4, self.set_arp_table)

  def get_tun_port(self, tun_end):
    "Get SUT port to tun_end"
    return self.ports['tun-%s' % tun_end]

  def insert_fakedrop_rules(self):
    if self.pl_conf.get('fakedrop', None) is None:
      return
    # Insert default drop actions for the sake of statistics
    mod_flow = self.mod_flow
    for table_name in self.pl.tables.iterkeys():
      if table_name != 'drop':
        mod_flow(table_name, 0, goto='drop')
    if not self.pl_conf.fakedrop:
      mod_flow('drop', 0)
    elif self.pl.has_tunnels:
      match = {'in_port': self.ul_port}
      mod_flow('drop', 1, match=match, output=self.ports['veth-main'])
      mod_flow('drop', 0, output=self.ul_port)
    else:
      # fakedrop == True and not has_tunnels
      mod_flow('drop', match={'in_port': self.ul_port}, output=self.dl_port)
      mod_flow('drop', match={'in_port': self.dl_port}, output=self.ul_port)

  def configure(self):
    if self.configured:
      return

    ofp = self.dp.ofproto
    parser = self.dp.ofproto_parser
    self.clear_switch()

    for bst in self.pl_conf.get('bsts', []):
      self.add_vxlan_tun('tun', bst)
    for cpe in self.pl_conf.get('cpe', []):
      self.add_vxlan_tun('tun', cpe)

    self.dp.send_msg(parser.OFPPortDescStatsRequest(self.dp, 0, ofp.OFPP_ANY))
    self.change_status('wait_for_PortDesc')
    # Will continue from self.configure_1()
