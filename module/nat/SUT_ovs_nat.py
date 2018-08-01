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

from ryu.ofproto import nicira_ext

from pipeline.base import PL_base

# http://ryu.readthedocs.io/en/latest/nicira_ext_ref.html
# http://www.openvswitch.org/support/dist-docs/ovs-ofctl.8.txt
# https://mail.openvswitch.org/pipermail/ovs-discuss/2016-July/041841.html

def action_ct(parser, kw):
  "Create a connection track action"
  args = {
    'flags': 0,
    'zone_src': '',
    'zone_ofs_nbits': 0,
    'recirc_table': 255,
    'alg': 0,
    'actions': [],
  }
  args.update(**kw)
  return parser.NXActionCT(**args)

def action_nat(parser, kw):
  "Create a NAT action"
  return parser.NXActionNAT(**kw)


class SUT_ovs(PL_base):
  """SNAT pipeline
  """
  def __init__(self, parent, conf):
    super(SUT_ovs, self).__init__(parent, conf)
    self.tables = {
      'tbl'  : 0,
      'drop' : 9,
    }

  def config_switch(self, parser):
    mod_flow = self.parent.mod_flow
    ul_port = self.parent.ul_port
    dl_port = self.parent.dl_port
    flag_commit = 1
    flag_snat = 1
    flag_dnat = 2
    ct_state_minus_trk = (0, 32)
    ct_state_plus_trk = (32, 32)

    # dl -> ul (priv -> pubilc, orig -> translated)
    #
    # ip,in_port="ul_port" actions=ct(commit,zone=1,nat(dst=dst)),output:"dl_port"
    match = {'in_port': dl_port, 'eth_type': 0x0800}
    actions = [
      action_ct(parser, {
        'flags': flag_commit,
        'zone_ofs_nbits': nicira_ext.ofs_nbits(0, 1),
        'actions': [
          action_nat(parser, {
            'flags': flag_dnat,
            'range_ipv4_min': self.conf.range_ipv4_min,
            'range_ipv4_max': self.conf.range_ipv4_max,
            'range_proto_min': self.conf.range_port_min,
            'range_proto_max': self.conf.range_port_max,
          })
        ]
      }),
      parser.OFPActionSetField(eth_dst=self.conf.uplink_dst_mac),
    ]
    mod_flow(match=match, actions=actions, output=ul_port)

    # ul -> dl (pubilc -> priv, translated -> orig)
    #
    # ct_state=-trk,ip,in_port="dl_port" actions=ct(table=0,zone=1,nat)
    match = {
      'in_port': ul_port,
      'eth_type': 0x0800,
      'ct_state': ct_state_minus_trk,
    }
    actions = [
      action_ct(parser, {
        'flags': 0,
        'zone_ofs_nbits': nicira_ext.ofs_nbits(0, 1),
        'recirc_table': 0,
        'actions': [ action_nat(parser, {'flags': 0}) ],
      }),
      parser.OFPActionSetField(eth_dst=self.conf.downlink_dst_mac),
    ]
    mod_flow(match=match, actions=actions, output=dl_port)

    # ct_state=+trk,ct_zone=1,in_port="dl_port" actions=output:"ul_port"
    match = {
      'in_port': ul_port,
      'eth_type': 0x0800,
      'ct_state': ct_state_plus_trk,
      'ct_zone': 1,
    }
    actions = [
      parser.OFPActionSetField(eth_dst=self.conf.downlink_dst_mac),
    ]
    mod_flow(match=match, actions=actions, output=dl_port)

