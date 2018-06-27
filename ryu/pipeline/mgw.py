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

from ryu.lib.packet.ether_types import ETH_TYPE_IP

from .base import PL_base

class PL(PL_base):

  def __init__(self, parent, conf):
    super(PL, self).__init__(parent, conf)
    self.has_tunnels = True
    self.tables = {
      'ingress'   : 0,
      'downlink'  : 3,
      'uplink'    : 4,
      'l3_lookup' : 7,
      'drop'      : 250
    }

  def get_tunnel_endpoints(self):
    return self.conf.bsts

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

    # Uplink: vxlan_port -> rate-limiter -> (FW->NAT) -> L3 lookup table
    if self.conf.name == 'bng':
      next_tbl = 'ul_fw'
    else:
      next_tbl = 'l3_lookup'
    match = {'tunnel_id': user.teid}
    inst = [parser.OFPInstructionMeter(meter_id=user.teid), goto(next_tbl)]
    mod_flow('uplink', match=match, inst=inst, cmd=cmd)

    # Downlink: (NAT->FW) -> rate-limiter -> vxlan_port
    match = {'eth_type': ETH_TYPE_IP, 'ipv4_dst': user.ip}
    out_port = self.parent.get_tun_port(user.tun_end)
    inst = [parser.OFPInstructionMeter(meter_id=user.teid)]
    actions = [parser.OFPActionSetField(tunnel_id=user.teid),
               parser.OFPActionOutput(out_port)]
    mod_flow('downlink', match=match, actions=actions, inst=inst, cmd=cmd)

  def mod_server(self, cmd, srv):
    self.logger.debug('%s-server: ip=%s' % (cmd, srv.ip))
    parser = self.parent.dp.ofproto_parser
    match = {'eth_type': ETH_TYPE_IP, 'ipv4_dst': srv.ip}
    action = parser.OFPActionGroup(srv.nhop)
    self.parent.mod_flow('l3_lookup', None, match, [action], cmd=cmd)

  def config_switch(self, parser):
    mod_flow = self.parent.mod_flow

    table = 'ingress'
    match = {'in_port': self.parent.ports['veth-main']}
    mod_flow('ingress', 9, match, [], [])
    next_table = {'mgw': 'downlink', 'bng': 'dl_nat'}[self.conf.name]
    match = {'in_port': self.parent.ul_port, 'eth_dst': self.conf.gw.mac}
    mod_flow('ingress', 9, match, goto=next_table)
    match = {'in_port': self.parent.ul_port}
    mod_flow('ingress', 8, match, goto='drop')
    mod_flow('ingress', 7, None, goto='uplink')

    for user in self.conf.users:
      self.mod_user('add', user)

    for i, nhop in enumerate(self.conf.nhops):
      out_port = nhop.port or self.parent.ul_port
      set_field = parser.OFPActionSetField
      self.parent.add_group(i, [set_field(eth_dst=nhop.dmac),
                                set_field(eth_src=nhop.smac),
                                parser.OFPActionOutput(out_port)])

    for srv in self.conf.srvs:
      self.mod_server('add', srv)

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
    out_port = self.parent.get_tun_port(new_bst)
    actions = [parser.OFPActionSetField(tunnel_id=user.teid),
               parser.OFPActionOutput(out_port)]
    inst = [parser.OFPInstructionMeter(meter_id=user.teid)]
    mod_flow('downlink', match=match, actions=actions, inst=inst, cmd='add')

  def do_add_user(self, action):
    self.mod_user('add', action.args)

  def do_del_user(self, action):
    self.mod_user('del', action.args)

  def do_add_server(self, action):
    self.mod_server('add', action.args)

  def do_del_server(self, action):
    self.mod_server('del', action.args)


