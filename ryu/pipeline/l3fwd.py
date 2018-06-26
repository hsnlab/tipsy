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

from ryu.lib.packet.ether_types import ETH_TYPE_IP, ETH_TYPE_ARP

from .base import PL_base

class PL(PL_base):

  def __init__(self, parent, conf):
    super(PL, self).__init__(parent, conf)
    self.tables = {
      'mac_fwd'             : 0,
      'arp_select'          : 1,
      'upstream_l3_table'   : 2,
      'downstream_l3_table' : 3,
      'drop'                : 4,
    }
    self.gr_next = 0
    self.gr_table = {}

  def config_switch(self, parser):
    ul_port = self.parent.ul_port
    dl_port = self.parent.dl_port

    # A basic MAC table lookup to check that the L2 header of the
    # receiver packet contains the router's own MAC address(es) in
    # which case forward to the =ARPselect= module, drop otherwise
    #
    # (We don't modify the hwaddr of a kernel interface, or set the
    # hwaddr of a dpdk interface, we just check whether incoming
    # packets have the correct addresses.)
    table = 'mac_fwd'
    match = {'in_port': ul_port,
             'eth_dst': self.conf.sut.ul_port_mac}
    self.parent.mod_flow(table, match=match, goto='arp_select')
    match = {'in_port': dl_port,
             'eth_dst': self.conf.sut.dl_port_mac}
    self.parent.mod_flow(table, match=match, goto='arp_select')

    # arp_select: direct ARP packets to the infra (unimplemented) and
    # IPv4 packets to the L3FIB for L3 processing, otherwise drop
    table = 'arp_select'
    match = {'eth_type': ETH_TYPE_ARP}
    self.parent.mod_flow(table, match=match, goto='drop')
    match = {'eth_type': ETH_TYPE_IP, 'in_port': dl_port}
    self.parent.mod_flow(table, match=match, goto='upstream_l3_table')
    match = {'eth_type': ETH_TYPE_IP, 'in_port': ul_port}
    self.parent.mod_flow(table, match=match, goto='downstream_l3_table')

    # L3FIB: perform longest-prefix-matching from an IP lookup table
    # and forward packets to the appropriate group table entry for
    # next-hop processing or drop if no matching L3 entry is found
    for d in ['upstream', 'downstream']:
      for entry in self.conf.get('%s_group_table' % d):
        self.add_group_table_entry(d, entry)

      for entry in self.conf.get('%s_l3_table' % d):
        self.mod_l3_table('add', d, entry)

  def mod_l3_table(self, cmd, table_prefix, entry):
    parser = self.parent.dp.ofproto_parser
    if table_prefix == 'upstream':
      gr_offset = 0
    else:
      gr_offset = len(self.conf.upstream_group_table)
    table = '%s_l3_table' % table_prefix
    addr = '%s/%s' % (entry.ip, entry.prefix_len)
    match = {'eth_type': ETH_TYPE_IP, 'ipv4_dst': addr}
    out_group = gr_offset + entry.nhop
    action = parser.OFPActionGroup(out_group)
    self.parent.mod_flow(table, match=match, actions=[action], cmd=cmd)

  def add_group_table_entry(self, direction, entry):
    parser = self.parent.dp.ofproto_parser
    port_name = '%sl_port' % direction[0]
    out_port = entry.port or self.parent.__dict__[port_name]
    actions = [parser.OFPActionSetField(eth_dst=entry.dmac),
               parser.OFPActionSetField(eth_src=entry.smac),
               parser.OFPActionOutput(out_port)]
    self.parent.add_group(self.gr_next, actions)
    self.gr_table[(entry.dmac, entry.smac)] = self.gr_next
    self.gr_next += 1

  def del_group_table_entry(self, entry):
    key = (entry.dmac, entry.smac)
    gr_id = self.gr_table[key]
    del self.gr_table[key]
    self.parent.del_group(gr_id)

    # We could be more clever here, but the run-time config always
    # deletes the last entry first.
    if gr_id == self.gr_next - 1:
      self.gr_next -= 1
    else:
      # Something unexpected.  We leave a hole in the group id space.
      self.logger.warn('Leakage in the group id space')
      self.logger.info('%s, %s', gr_id, self.gr_next)

  def do_mod_l3_table(self, args):
    self.mod_l3_table(args.cmd, args.table, args.entry)

  def do_mod_group_table(self, args):
    if args.cmd == 'add':
      self.add_group_table_entry(args.table, args.entry)
    elif args.cmd == 'del':
      self.del_group_table_entry(args.entry)
    else:
      self.logger.error('%s: unknown cmd (%s)', args.action, args.cmd)


