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

from ryu.lib.packet import in_proto
from ryu.lib.packet.ether_types import ETH_TYPE_IP

import find_mod
Base = find_mod.find_class('SUT_erfs', 'mgw')

class SUT_erfs(Base):

  def __init__(self, parent, conf):
    super(SUT_erfs, self).__init__(parent, conf)
    self.tables = {
      'mac_fwd'   : 0,
      'arp_select': 1,
      'dir_select': 2,
      'dl_nat'    : 3,
      'dl_fw'     : 4,
      'downlink'  : 5,
      'uplink'    : 6,
      'ul_fw'     : 7,
      'ul_nat'    : 8,
      'l3_lookup' : 9,
      'drop'      : 99
    }

  def get_tunnel_endpoints(self):
    return self.conf.cpe

  def add_fw_rules(self, table_name, rules, next_table):
    if not rules:
      return

    mod_flow = self.parent.mod_flow
    parser = self.parent.dp.ofproto_parser

    for rule in rules:
      # TODO: ip_proto, ip mask, port mask (?)
      match = {
        'eth_type': ETH_TYPE_IP,
        'ip_proto': in_proto.IPPROTO_TCP,
        'ipv4_src': (rule.src_ip, '255.255.255.0'),
        'ipv4_dst': (rule.dst_ip, '255.255.255.0'),
        'tcp_src': rule.src_port,
        'tcp_dst': rule.dst_port,
      }
      mod_flow(table_name, match=match, goto='drop')

    # We added a default rule at priority=0, in erfs we cannot add
    # another one at priority=1, so we match eth_type, which should be
    # unnecessary at this point.
    match = {'eth_type': ETH_TYPE_IP}
    mod_flow(table_name, priority=1, match=match, goto=next_table)

  @staticmethod
  def get_proto_name (ip_proto_num):
    name = {in_proto.IPPROTO_TCP: 'tcp',
            in_proto.IPPROTO_UDP: 'udp'}.get(ip_proto_num)
    #TODO: handle None
    return name

  def add_ul_nat_rules (self, table_name, next_table):
    mod_flow = self.parent.mod_flow
    parser = self.parent.dp.ofproto_parser

    for rule in self.conf.nat_table:
      proto_name = self.get_proto_name(rule.proto)
      match = {'eth_type': ETH_TYPE_IP,
               'ipv4_src': (rule.priv_ip, '255.255.255.255'),
               'ip_proto': rule.proto,
               proto_name + '_src': rule.priv_port}
      actions = [{'ipv4_src': rule.pub_ip},
                 {proto_name + '_src': rule.pub_port}]
      actions = [parser.OFPActionSetField(**a) for a in actions]
      mod_flow(table_name, match=match, actions=actions, goto=next_table)

  def add_dl_nat_rules (self, table_name, next_table):
    mod_flow = self.parent.mod_flow
    parser = self.parent.dp.ofproto_parser

    for rule in self.conf.nat_table:
      proto_name = self.get_proto_name(rule.proto)
      match = {'eth_type': ETH_TYPE_IP,
               'ipv4_dst': (rule.pub_ip, '255.255.255.255'),
               'ip_proto': rule.proto,
               proto_name + '_dst': rule.pub_port}
      actions = [{'ipv4_dst': rule.priv_ip},
                 {proto_name + '_dst': rule.priv_port}]
      actions = [parser.OFPActionSetField(**a) for a in actions]
      mod_flow(table_name, match=match, actions=actions, goto=next_table)

  def config_switch(self, parser):
    super(SUT_erfs, self).config_switch(parser)

    self.add_fw_rules('ul_fw', self.conf.ul_fw_rules, 'ul_nat')
    self.add_fw_rules('dl_fw', self.conf.dl_fw_rules, 'downlink')
    self.add_ul_nat_rules('ul_nat', 'l3_lookup')
    self.add_dl_nat_rules('dl_nat', 'dl_fw')
