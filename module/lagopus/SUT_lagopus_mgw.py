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

class SUT_lagopus(Base):

  def init_backend(self):
    # Backend specific initialization
    pass

  def get_vxlan_encap_actions(self, vxlan_vni, tun_ip_src, tun_ip_dst):
    # https://github.com/lagopus/lagopus/issues/92
    ofp = self.parent.dp.ofproto
    parser = self.parent.dp.ofproto_parser

    type_eth  = (ofp.OFPHTN_ONF << 16) | ofp.OFPHTO_ETHERNET
    type_ip   = (ofp.OFPHTN_ETHERTYPE << 16) | 0x0800
    type_udp  = (ofp.OFPHTN_IP_PROTO << 16) | 17
    type_vxl  = (ofp.OFPHTN_UDP_TCP_PORT << 16) | 4789
    type_next = (ofp.OFPHTN_ONF << 16) | ofp.OFPHTO_USE_NEXT_PROTO
    actions = [
      parser.OFPActionEncap(type_vxl),
      parser.OFPActionSetField(vxlan_vni=vxlan_vni),
      parser.OFPActionEncap(type_udp),
      parser.OFPActionSetField(udp_src=5432),
      parser.OFPActionSetField(udp_dst=4789),
      parser.OFPActionEncap(type_ip),
      parser.OFPActionSetField(ipv4_dst=tun_ip_dst),
      parser.OFPActionSetField(ipv4_src=tun_ip_src),
      parser.OFPActionSetNwTtl(nw_ttl=64),
      parser.OFPActionEncap(type_eth),
      # parser.OFPActionSetField(eth_src='12:22:22:22:22:22'),
      # parser.OFPActionSetField(eth_dst='22:33:33:33:33:33'),
    ]
    return actions

  def get_vxlan_decap_actions(self):
    # https://github.com/lagopus/lagopus/issues/92
    ofp = self.parent.dp.ofproto
    parser = self.parent.dp.ofproto_parser

    type_eth  = (ofp.OFPHTN_ONF << 16) | ofp.OFPHTO_ETHERNET
    type_ip   = (ofp.OFPHTN_ETHERTYPE << 16) | 0x0800
    type_udp  = (ofp.OFPHTN_IP_PROTO << 16) | 17
    type_vxl  = (ofp.OFPHTN_UDP_TCP_PORT << 16) | 4789
    type_next = (ofp.OFPHTN_ONF << 16) | ofp.OFPHTO_USE_NEXT_PROTO

    actions = [parser.OFPActionDecap(type_eth, type_ip),
               parser.OFPActionDecap(type_ip,  type_udp),
               parser.OFPActionDecap(type_udp, type_vxl),
               parser.OFPActionDecap(type_vxl, type_next),
    ]
    return actions

  def get_vxlan_decap_actions_before_match(self):
    # As opposed to erfs, we cannot decap vxlan header just now,
    # becasue that doesn't copy the tunnel-id to a metadata filed
    # actions = self.get_vxlan_decap_actions()
    return []

  def get_vxlan_match_and_decap(self, vxlan_vni):
    # https://github.com/lagopus/lagopus/issues/92
    match = {'eth_type': ETH_TYPE_IP, 'ip_proto': in_proto.IPPROTO_UDP,
             'vxlan_vni': vxlan_vni, 'udp_src': 4789}
    actions = self.get_vxlan_decap_actions()
    return match, actions
