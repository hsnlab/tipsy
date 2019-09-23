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
Base = find_mod.find_class('SUT_openflow', 'base')

class SUT_openflow(Base):

  def __init__(self, parent, conf):
    super(SUT_openflow, self).__init__(parent, conf)
    self.tables = {
      'zero' : 0,
      'one'  : 1,
      'two'  : 2,
      'drop' : 28,  # max table_id for NoviFlow Inc ns1132
    }

  def config_switch_universal(self, parser):
    for s_idx, s in enumerate(self.conf.service):
      self.mod_service_universal(parser, 'add', s_idx, s)

  def mod_service_universal(self, parser, cmd, s_idx, s):
    mod_flow = self.parent.mod_flow
    out_port = self.parent.ul_port
    for b in s.backend:
      match = {'eth_type': ETH_TYPE_IP,
               'ipv4_src': '%s/%s' % (b.ip_src, b.prefix_len),
               'ipv4_dst': '%s/32' % s.ip_dst,
               'ip_proto': in_proto.IPPROTO_UDP,
               'udp_dst' : int(s.udp_dst),
      }
      mod_flow('zero', cmd=cmd, match=match, output=out_port)

  def config_switch_metadata(self, parser):
    mod_flow = self.parent.mod_flow
    out_port = self.parent.ul_port
    if self.noviflow:
      mod_flow('zero', goto='one')
      self.tbl_gw = 'one'
      self.tbl_lb = 'two'
    else:
      self.tbl_gw = 'zero'
      self.tbl_lb = 'one'
    for s_idx, s in enumerate(self.conf.service):
      self.mod_service_metadata(parser, 'add', s_idx, s)
      for b in s.backend:
        match = {'eth_type': ETH_TYPE_IP,
                 'metadata': s_idx,
                 'ipv4_src': '%s/%s' % (b.ip_src, b.prefix_len),
        }
        mod_flow(self.tbl_lb, match=match, output=out_port)

  def mod_service_metadata(self, parser, cmd, s_idx, s):
    mod_flow = self.parent.mod_flow
    match = {'eth_type': ETH_TYPE_IP,
             'ipv4_dst': '%s/32' % s.ip_dst,
             'ip_proto': in_proto.IPPROTO_UDP,
             'udp_dst' : int(s.udp_dst),
    }
    inst = [parser.OFPInstructionWriteMetadata(s_idx, (1 << 64) - 1)]
    mod_flow(self.tbl_gw, match=match, inst=inst, goto=self.tbl_lb)

  def config_switch_goto(self, parser):
    mod_flow = self.parent.mod_flow
    out_port = self.parent.ul_port
    for s_idx, s in enumerate(self.conf.service):
      self.mod_service_goto(parser, 'add', s_idx, s)
      for b in s.backend:
        match = {'eth_type': ETH_TYPE_IP,
                 'ipv4_src': '%s/%s' % (b.ip_src, b.prefix_len),
        }
        mod_flow((s_idx + 1), match=match, output=out_port)

  def mod_service_goto(self, parser, cmd, s_idx, s):
    mod_flow = self.parent.mod_flow
    match = {'eth_type': ETH_TYPE_IP,
             'ipv4_dst': '%s/32' % s.ip_dst,
             'ip_proto': in_proto.IPPROTO_UDP,
             'udp_dst' : int(s.udp_dst),
    }
    inst = [parser.OFPInstructionGotoTable(s_idx + 1)]
    mod_flow('zero', match=match, inst=inst)

  def config_switch(self, parser):
    mfr = self.parent.result.get('mfr_desc')
    hw = self.parent.result.get('hw_desc')
    if mfr == 'NoviFlow Inc' and hw == 'NS1132':
      self.noviflow = True

    itype = self.parent.bm_conf.pipeline.implementation_type
    attr = getattr(self, 'config_switch_%s' % itype)
    attr(parser)

  def do_mod_port(self, args):
    parser = self.parent.dp.ofproto_parser
    itype = self.parent.bm_conf.pipeline.implementation_type
    attr = getattr(self, 'mod_service_%s' % itype)
    s_idx = int(args.args.s_idx)
    service = self.conf.service[s_idx]

    attr(parser, 'del', s_idx, service)

    msg = parser.OFPBarrierRequest(self.parent.dp)
    self.parent.dp.send_msg(msg)

    attr(parser, 'add', s_idx, service)
