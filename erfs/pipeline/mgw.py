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

import sys

from ryu.lib.packet import in_proto
from ryu.lib.packet.ether_types import ETH_TYPE_IP, ETH_TYPE_ARP
from ryu.ofproto import ofproto_v1_3


import exp_ericsson as eri
import sw_conf_erfs as sw_conf
eri.register(ofproto_v1_3)

from ovs.mgw import PL as PL_ovs_mgw

class PL(PL_ovs_mgw):

  def __init__(self, parent, conf):
    super(PL, self).__init__(parent, conf)
    self.has_tunnels = True
    self.group_idx = 0
    self.tables = {
      'mac_fwd'   : 0,
      'arp_select': 1,
      'dir_select': 2,
      'downlink'  : 3,
      'uplink'    : 4,
      'l3_lookup' : 5,
      'drop'      : 9
    }

  def mod_user(self, cmd='add', user=None):
    self.logger.debug('%s-user: teid=%d' % (cmd, user.teid))
    ofp = self.parent.dp.ofproto
    parser = self.parent.dp.ofproto_parser
    goto = self.parent.goto
    mod_flow = self.parent.mod_flow

    if user.teid == 0:
      # meter_id = teid, and meter_id cannot be 0
      self.logger.warn('Skipping user (teid==0)')
      return

    # Create per user meter
    command = {'add': ofp.OFPMC_ADD, 'del': ofp.OFPMC_DELETE}[cmd]
    band = parser.OFPMeterBandDrop(rate=user.rate_limit/1000) # kbps
    msg = parser.OFPMeterMod(self.parent.dp, command=command,
                             meter_id=user.teid, bands=[band])
    self.parent.dp.send_msg(msg)

    # Uplink: dl_port -> vxlan_pop -> rate-lim -> (FW->NAT) -> L3 lookup tbl
    if self.conf.name == 'bng':
      next_tbl = 'ul_fw'
    else:
      next_tbl = 'l3_lookup'
    match = {'tunnel_id': user.teid}
    inst = [parser.OFPInstructionMeter(meter_id=user.teid), goto(next_tbl)]
    mod_flow('uplink', match=match, inst=inst, cmd=cmd)

    # Downlink: (NAT->FW) -> rate-limiter -> vxlan push
    match = {'eth_type': ETH_TYPE_IP, 'ipv4_dst': user.ip}
    tun_id = user.teid
    tun_ip_src = self.conf.gw.ip
    tun_ip_dst = self.get_tunnel_endpoints()[user.tun_end].ip

    inst = [parser.OFPInstructionMeter(meter_id=user.teid),
            goto('l3_lookup')]
    actions = [eri.EricssonActionPushVXLAN(user.teid),
               parser.OFPActionSetField(ipv4_dst=tun_ip_dst),
               parser.OFPActionSetField(ipv4_src=tun_ip_src)]
    mod_flow('downlink', match=match, actions=actions, inst=inst, cmd=cmd)

  def add_bst_or_cpe(self, obj):
    self.logger.debug('add-bst-or-cpe: ip=%s' % obj.ip)
    parser = self.parent.dp.ofproto_parser

    # add group-table entry
    out_port = obj.port or self.parent.dl_port
    set_field = parser.OFPActionSetField
    self.parent.add_group(self.group_idx,
                          [set_field(eth_dst=obj.mac),
                           set_field(eth_src=self.conf.gw.mac),
                           parser.OFPActionOutput(out_port)])

    # add l3_lookup entry
    match = {'eth_type': ETH_TYPE_IP, 'ipv4_dst': obj.ip}
    action = parser.OFPActionGroup(self.group_idx)
    self.parent.mod_flow('l3_lookup', None, match, [action], cmd='add')

    obj.group_idx = self.group_idx
    self.group_idx +=1

  def config_switch(self, parser):
    mod_flow = self.parent.mod_flow
    goto = self.parent.goto
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
             'eth_dst': self.conf.gw.mac}
    self.parent.mod_flow(table, match=match, goto='arp_select')
    match = {'in_port': dl_port,
             'eth_dst': self.conf.gw.mac}
    self.parent.mod_flow(table, match=match, goto='arp_select')

    # arp_select: direct ARP packets to the infra (unimplemented) and
    # IPv4 packets to the L3FIB for L3 processing, otherwise drop
    table = 'arp_select'
    match = {'eth_type': ETH_TYPE_ARP}
    self.parent.mod_flow(table, match=match, goto='drop')
    match = {'eth_type': ETH_TYPE_IP, 'in_port': dl_port}
    self.parent.mod_flow(table, match=match, goto='dir_select')
    match = {'eth_type': ETH_TYPE_IP, 'in_port': ul_port}
    self.parent.mod_flow(table, match=match, goto='dir_select')

    #
    table = 'dir_select'
    match = {'eth_type': ETH_TYPE_IP, 'ipv4_dst': self.conf.gw.ip,
             'ip_proto': in_proto.IPPROTO_UDP, 'udp_src': 4789}
    actions = [eri.EricssonActionPopVXLAN()]
    self.parent.mod_flow(table, priority=2, match=match,
                         actions=actions, goto='uplink')
    # Downlink, should check: IP in UE range, instead: check for ether_type
    match = {'eth_type': ETH_TYPE_IP}
    next_tbl= {'mgw': 'downlink', 'bng': 'dl_nat'}[self.conf.name]
    self.parent.mod_flow(table, priority=1, match=match, goto=next_tbl)

    for user in self.conf.users:
      self.mod_user('add', user)

    for nhop in self.conf.nhops:
      out_port = nhop.port or self.parent.ul_port
      set_field = parser.OFPActionSetField
      self.parent.add_group(self.group_idx,
                            [set_field(eth_dst=nhop.dmac),
                             set_field(eth_src=nhop.smac),
                             parser.OFPActionOutput(out_port)])
      self.group_idx += 1
    for srv in self.conf.srvs:
      self.mod_server('add', srv)

    if self.conf.name == 'bng':
      objs = self.conf.cpe
    else:
      objs = self.conf.bsts
    for obj in objs:
      self.add_bst_or_cpe(obj)

  def do_handover(self, action):
    parser = self.parent.dp.ofproto_parser
    mod_flow = self.parent.mod_flow
    log = self.logger.debug
    user_idx= action.args.user_teid - 1
    user = self.conf.users[user_idx]
    old_bst = user.tun_end
    new_bst = (user.tun_end + action.args.bst_shift) % len(self.conf.bsts)
    log("handover user.%s: tun_end.%s -> tun_end.%s" %
        (user.teid, old_bst, new_bst))
    user.tun_end = new_bst
    self.conf.users[user_idx] = user

    # Downlink: rate-limiter -> vxlan_port
    match = {'eth_type': ETH_TYPE_IP, 'ipv4_dst': user.ip}
    tun_id = user.teid
    tun_ip_src = self.conf.gw.ip
    tun_ip_dst = self.get_tunnel_endpoints()[user.tun_end].ip

    inst = [parser.OFPInstructionMeter(meter_id=user.teid),
            self.parent.goto('l3_lookup')]
    actions = [eri.EricssonActionPushVXLAN(user.teid),
               parser.OFPActionSetField(ipv4_dst=tun_ip_dst),
               parser.OFPActionSetField(ipv4_src=tun_ip_src)]
    mod_flow('downlink', match=match, actions=actions, inst=inst, cmd='add')

