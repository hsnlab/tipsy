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
"""
TIPSY: Telco pIPeline benchmarking SYstem

Run as:
    $ cd path/to/tipsy.py
    $ ryu-manager --config-dir .

The setup is similar to the left side of the figure in
http://docs.openvswitch.org/en/latest/howto/userspace-tunneling/
"""

import datetime
import json
import os
import re
import requests
import signal
import subprocess
import time

from ryu import cfg
from ryu import utils
from ryu.app.wsgi import ControllerBase
from ryu.app.wsgi import Response
from ryu.app.wsgi import WSGIApplication
from ryu.app.wsgi import CONF as wsgi_conf
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER
from ryu.controller.handler import HANDSHAKE_DISPATCHER
from ryu.controller.handler import MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub
from ryu.lib import ofctl_utils as ofctl
from ryu.lib.packet import in_proto
from ryu.lib.packet.ether_types import ETH_TYPE_IP, ETH_TYPE_ARP
from ryu.ofproto import ofproto_v1_3
from ryu.services.protocols.bgp.utils.evtlet import LoopingCall

import ip
import sw_conf_vsctl as sw_conf

fdir = os.path.dirname(os.path.realpath(__file__))
pipeline_conf = os.path.join(fdir, 'pipeline.json')
benchmark_conf = os.path.join(fdir, 'benchmark.json')
cfg.CONF.register_opts([
  cfg.StrOpt('pipeline_conf', default=pipeline_conf,
             help='json formatted configuration file of the pipeline'),
  cfg.StrOpt('benchmark_conf', default=benchmark_conf,
             help='configuration of the whole benchmark (in json)'),
  cfg.StrOpt('webhook_configured', default='http://localhost:8888/configured',
             help='URL to request when the sw is configured'),
], group='tipsy')
CONF = cfg.CONF['tipsy']


###########################################################################


class ObjectView(object):
  def __init__(self, **kwargs):
    self.__dict__.update(kwargs)

  def __repr__(self):
    return self.__dict__.__repr__()

  def __getattr__(self, name):
    return self.__dict__[name.replace('_', '-')]

  def get (self, attr, default=None):
    return self.__dict__.get(attr, default)


class PL(object):
  def __init__(self, parent, conf):
    self.conf = conf
    self.parent = parent
    self.logger = self.parent.logger
    self.has_tunnels = False
    self.tables = {'drop': 0}

  def get_tunnel_endpoints(self):
    raise NotImplementedError

  def do_unknown(self, action):
    self.logger.error('Unknown action: %s' % action.action)


class PL_portfwd(PL):
  """L2 Port Forwarding

  In the upstream direction the pipeline will receive L2 packets from the
  downlink port of the SUT and forward them to the uplink port. Meanwhile, it
  may optionally rewrite the source MAC address in the L2 frame to the MAC
  address of the uplink port (must be specified by the pipeline config).  The
  downstream direction is the same, but packets are received from the uplink
  port and forwarded to the downlink port after an optional MAC rewrite.
  """
  def __init__(self, parent, conf):
    super(PL_portfwd, self).__init__(parent, conf)
    self.tables = {
      'tbl'  : 0,
    }

  def config_switch(self, parser):
    mod_flow = self.parent.mod_flow
    ul_port = self.parent.ul_port
    dl_port = self.parent.dl_port

    actions = []
    mac = self.conf.mac_swap_downstream
    if mac:
      actions = [parser.OFPActionSetField(eth_src=mac)]
    match = {'in_port': dl_port}
    mod_flow(match=match, actions=actions, output=ul_port)

    actions = []
    mac = self.conf.mac_swap_upstream
    if mac:
      actions = [parser.OFPActionSetField(eth_src=mac)]
    match = {'in_port': ul_port}
    mod_flow(match=match, actions=actions, output=dl_port)


class PL_l2fwd(PL):
  """L2 Packet Forwarding

  Upstream the L2fwd pipeline will receive packets from the downlink
  port, perform a lookup for the destination MAC address in a static
  MAC table, and if a match is found the packet will be forwarded to
  the uplink port or otherwise dropped (or likewise forwarded upstream
  if the =fakedrop= parameter is set to =true=).  The downstream
  pipeline is just the other way around, but note that the upstream
  and downstream pipelines use separate MAC tables.
  """

  def __init__(self, parent, conf):
    super(PL_l2fwd, self).__init__(parent, conf)
    self.tables = {
      'selector'   : 0,
      'upstream'   : 1,
      'downstream' : 2,
      'drop'       : 3,
    }

  def config_switch(self, parser):
    ul_port = self.parent.ul_port
    dl_port = self.parent.dl_port

    table = 'selector'
    self.parent.mod_flow(table, match={'in_port': dl_port}, goto='upstream')
    self.parent.mod_flow(table, match={'in_port': ul_port}, goto='downstream')

    for d in ['upstream', 'downstream']:
      for entry in self.conf.get('%s-table' % d):
        self.mod_table('add', d, entry)

  def mod_table(self, cmd, table, entry):
    mod_flow = self.parent.mod_flow
    out_port = {'upstream': self.parent.ul_port,
                'downstream': self.parent.dl_port}[table]
    out_port = entry.out_port or out_port

    mod_flow(table, match={'eth_dst': entry.mac}, output=out_port, cmd=cmd)

  def do_mod_table(self, args):
    self.mod_table(args.cmd, args.table, args.entry)


class PL_l3fwd(PL):

  def __init__(self, parent, conf):
    super(PL_l3fwd, self).__init__(parent, conf)
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


class PL_mgw(PL):

  def __init__(self, parent, conf):
    super(PL_mgw, self).__init__(parent, conf)
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
    next_table = {'mgw': 'downlink', 'bng': 'dl_fw'}[self.conf.name]
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


class PL_bng(PL_mgw):

  def __init__(self, parent, conf):
    super(PL_bng, self).__init__(parent, conf)
    self.tables = {
      'ingress'   : 0,
      'dl_nat'    : 1,
      'dl_fw'     : 2,
      'downlink'  : 3,
      'uplink'    : 4,
      'ul_fw'     : 5,
      'ul_nat'    : 6,
      'l3_lookup' : 7,
      'drop'      : 250
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
    mod_flow(table_name, priority=1, goto=next_table)

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
    super(PL_bng, self).config_switch(parser)

    self.add_fw_rules('ul_fw', self.conf.ul_fw_rules, 'ul_nat')
    self.add_fw_rules('dl_fw', self.conf.dl_fw_rules, 'downlink')
    self.add_ul_nat_rules('ul_nat', 'l3_lookup')
    self.add_dl_nat_rules('dl_nat', 'dl_fw')


class Tipsy(app_manager.RyuApp):
  OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
  _CONTEXTS = { 'wsgi': WSGIApplication }
  _instance = None

  def __init__(self, *args, **kwargs):
    super(Tipsy, self).__init__(*args, **kwargs)
    Tipsy._instance = self
    self.logger.debug(" __init__()")

    self.lock = False
    self.dp_id = None
    self.configured = False
    self.dl_port = None # port numbers in the OpenFlow switch
    self.ul_port = None #
    self.status = 'init'

    self.logger.debug("%s, %s" % (args, kwargs))

    self.parse_conf('pl_conf', CONF['pipeline_conf'])
    self.parse_conf('bm_conf', CONF['benchmark_conf'])
    # port "names" used to configure the OpenFlow switch
    self.dl_port_name = self.bm_conf.sut.downlink_port
    self.ul_port_name = self.bm_conf.sut.uplink_port
    try:
      self.pl = globals()['PL_%s' % self.pl_conf.name](self, self.pl_conf)
    except (KeyError, NameError) as e:
      self.logger.error('Failed to instanciate pipeline (%s): %s' %
                        (self.pl_conf.name, e))
      raise(e)

    self._timer = LoopingCall(self.handle_timer)

    wsgi = kwargs['wsgi']
    self.waiters = {}
    self.data = {'waiters': self.waiters}

    mapper = wsgi.mapper
    wsgi.registory['TipsyController'] = self.data
    for attr in dir(TipsyController):
      if attr.startswith('get_'):
        mapper.connect('tipsy', '/tipsy/' + attr[len('get_'):],
                       controller=TipsyController, action=attr,
                       conditions=dict(method=['GET']))

    self.initialize_datapath()
    self.change_status('wait')  # Wait datapath to connect

  def parse_conf(self, var_name, fname):
    self.logger.info("conf_file: %s" % fname)

    try:
      with open(fname, 'r') as f:
        conv_fn = lambda d: ObjectView(**d)
        config = json.load(f, object_hook=conv_fn)
    except IOError as e:
      self.logger.error('Failed to load cfg file (%s): %s' %
                        (fname, e))
      raise(e)
    except ValueError as e:
      self.logger.error('Failed to parse cfg file (%s): %s' %
                        (fname, e))
      raise(e)
    setattr(self, var_name, config)

  def change_status(self, new_status):
    self.logger.info("status: %s -> %s" % (self.status, new_status))
    self.status = new_status

  def get_status(self, **kw):
    return self.status

  def handle_timer(self):
    self.logger.warn("timer called %s",  datetime.datetime.now())
    if self.lock:
      self.logger.error('Previous handle_timer is still running')
      self._timer.stop()
      raise Exception('Previous handle_timer is still running')
    self.lock = True

    for cmd in self.pl_conf.run_time:
      attr = getattr(self.pl, 'do_%s' % cmd.action, self.pl.do_unknown)
      attr(cmd)

    #time.sleep(0.5)
    self.logger.warn("time      :  %s",  datetime.datetime.now())

    self.lock = False

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

  @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
  def handle_switch_features(self, ev):
    if self.dp_id and self.dp_id != ev.msg.datapath.id:
      self.logger.error("This app can control only one switch")
      raise Exception("This app can control only one switch")
    if self.dp_id is not None:
      self.logger.info("Switch has reconnected, reconfiguring")

    self.configured = False
    self.dp = ev.msg.datapath
    self.dp_id = self.dp.id
    ofp = self.dp.ofproto
    parser = self.dp.ofproto_parser
    self.logger.info("switch_features: datapath:%s, ofproto:%s" %
                     (self.dp.id, ofp.OFP_VERSION))
    self.change_status('connected')

    self.dp.send_msg( parser.OFPDescStatsRequest(self.dp, 0) )

    self.configure()

  @set_ev_cls(ofp_event.EventOFPDescStatsReply, MAIN_DISPATCHER)
  def handle_desc_stats_reply(self, ev):
    self.logger.info(str(ev.msg.body))

  @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
  def handle_port_desc_stats_reply(self, ev):
    ofp = self.dp.ofproto

    # Map port names in cfg to actual OF port numbers
    if self.dl_port_name == self.ul_port_name:
      self.dl_port_name = self.ul_port_name = 'in_port'
    self.ports = {'in_port': ofp.OFPP_IN_PORT}
    for port in ev.msg.body:
      self.ports[port.name] = port.port_no
    for name in sorted(self.ports):
      self.logger.debug('port: %s, %s' % (name, self.ports[name]))

    if self.pl.has_tunnels:
      ports = ['ul_port']
    else:
      ports = ['ul_port', 'dl_port']
    for spec_port in ports:
      port_name = getattr(self, '%s_name' % spec_port)
      if self.ports.get(port_name):
        # kernel interface -> OF returns the interface name as port_name
        port_no = self.ports[port_name]
        self.__dict__[spec_port] = port_no
        self.logger.info('%s (%s): %s' % (spec_port, port_name, port_no))
      elif self.ports.get(spec_port):
        # dpdk interface -> OF returns the "logical" br name as port_name
        port_no = self.ports[spec_port]
        self.__dict__[spec_port] = port_no
        self.logger.info('%s (%s): %s' % (spec_port, port_name, port_no))
      else:
        self.logger.critical('%s (%s): not found' % (spec_port, port_name))
    self.configure_1()

  @set_ev_cls(ofp_event.EventOFPErrorMsg,
              [HANDSHAKE_DISPATCHER, CONFIG_DISPATCHER, MAIN_DISPATCHER])
  def handle_error_msg(self, ev):
    msg = ev.msg
    ofp = self.dp.ofproto

    if msg.type == ofp.OFPET_METER_MOD_FAILED:
      cmd = 'ovs-vsctl set bridge s1 datapath_type=netdev'
      self.logger.error('METER_MOD failed, "%s" might help' % cmd)
    elif msg.type and msg.code:
      self.logger.error('OFPErrorMsg received: type=0x%02x code=0x%02x '
                        'message=%s',
                        msg.type, msg.code, utils.hex_array(msg.data))
    else:
      self.logger.error('OFPErrorMsg received: %s', msg)

  def goto(self, table_name):
    "Return a goto insturction to table_name"
    parser = self.dp.ofproto_parser
    return parser.OFPInstructionGotoTable(self.pl.tables[table_name])

  def get_tun_port(self, tun_end):
    "Get SUT port to tun_end"
    return self.ports['tun-%s' % tun_end]

  def mod_flow(self, table=0, priority=None, match=None,
               actions=None, inst=None, out_port=None, out_group=None,
               output=None, goto=None, cmd='add'):

    ofp = self.dp.ofproto
    parser = self.dp.ofproto_parser

    if actions is None:
      actions = []
    if inst is None:
      inst = []
    if type(table) in [str, unicode]:
      table = self.pl.tables[table]
    if priority is None:
      priority = ofp.OFP_DEFAULT_PRIORITY
    if output:
      actions.append(parser.OFPActionOutput(output))
    if goto:
      inst.append(self.goto(goto))
    if cmd == 'add':
      command=ofp.OFPFC_ADD
    elif cmd == 'del':
      command=ofp.OFPFC_DELETE
    else:
      command=cmd

    if type(match) == dict:
      match = parser.OFPMatch(**match)

    if out_port is None:
      out_port = ofp.OFPP_ANY
    if out_group is None:
      out_group=ofp.OFPG_ANY

    # Construct flow_mod message and send it.
    if actions:
      inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS,
                                           actions)] + inst
    msg = parser.OFPFlowMod(datapath=self.dp,  table_id=table,
                            priority=priority, match=match,
                            instructions=inst, command=command,
                            out_port=out_port, out_group=out_group)
    self.dp.send_msg(msg)

  def add_group(self, gr_id, actions, gr_type=None):
    ofp = self.dp.ofproto
    parser = self.dp.ofproto_parser
    gr_type = gr_type or ofp.OFPGT_INDIRECT

    weight = 0
    watch_port = ofp.OFPP_ANY
    watch_group = ofp.OFPG_ANY
    buckets = [parser.OFPBucket(weight, watch_port, watch_group, actions)]

    req = parser.OFPGroupMod(self.dp, ofp.OFPGC_ADD, gr_type, gr_id, buckets)
    self.dp.send_msg(req)

  def del_group(self, gr_id, gr_type=None):
    ofp = self.dp.ofproto
    parser = self.dp.ofproto_parser
    gr_type = gr_type or ofp.OFPGT_INDIRECT

    req = parser.OFPGroupMod(self.dp, ofp.OFPGC_DELETE, gr_type, gr_id)
    self.dp.send_msg(req)

  def clear_table(self, table_id):
    parser = self.dp.ofproto_parser
    ofp = self.dp.ofproto
    clear = parser.OFPFlowMod(self.dp,
                              table_id=table_id,
                              command=ofp.OFPFC_DELETE,
                              out_port=ofp.OFPP_ANY,
                              out_group=ofp.OFPG_ANY)
    self.dp.send_msg(clear)

  def clear_switch(self):
    for table_id in self.pl.tables.values():
      self.clear_table(table_id)

    # Delete all meters
    parser = self.dp.ofproto_parser
    ofp = self.dp.ofproto
    clear = parser.OFPMeterMod(self.dp,
                               command=ofp.OFPMC_DELETE,
                               meter_id=ofp.OFPM_ALL)
    self.dp.send_msg(clear)

    # Delete all groups
    clear = parser.OFPGroupMod(self.dp,
                               ofp.OFPGC_DELETE,
                               ofp.OFPGT_INDIRECT,
                               ofp.OFPG_ALL)
    self.dp.send_msg(clear)

    # Delete tunnels of old base-stations
    sw_conf.del_old_ports(self.dp_id)

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

  def configure_1(self):
    self.change_status('configure_1')
    parser = self.dp.ofproto_parser

    self.insert_fakedrop_rules()
    self.pl.config_switch(parser)

    # Finally, send and wait for a barrier
    msg = parser.OFPBarrierRequest(self.dp)
    msgs = []
    ofctl.send_stats_request(self.dp, msg, self.waiters, msgs, self.logger)

    self.handle_configured()

  def handle_configured(self):
    "Called when initial configuration is uploaded to the switch"

    self.configured = True
    self.change_status('configured')
    try:
      requests.get(CONF['webhook_configured'])
    except requests.ConnectionError:
      pass
    if self.pl_conf.get('run_time'):
      self._timer.start(1)
    # else:
    #   hub.spawn_after(1, TipsyController.do_exit)

  def stop(self):
    self.change_status('stopping')
    self.stop_datapath()
    self.close()
    self.change_status('stopped')


# TODO?: https://stackoverflow.com/questions/12806386/standard-json-api-response-format
def rest_command(func):
  def _rest_command(*args, **kwargs):
    try:
      msg = func(*args, **kwargs)
      return Response(content_type='application/json',
                      body=json.dumps(msg))

    except SyntaxError as e:
      status = 400
      details = e.msg
    except (ValueError, NameError) as e:
      status = 400
      details = e.message

    except Exception as msg:
      status = 404
      details = str(msg)

    msg = {'result': 'failure',
           'details': details}
    return Response(status=status, body=json.dumps(msg))

  return _rest_command

class TipsyController(ControllerBase):

  def __init__(self, req, link, data, **config):
    super(TipsyController, self).__init__(req, link, data, **config)

  @rest_command
  def get_status(self, req, **kw):
    return Tipsy._instance.get_status()

  @rest_command
  def get_exit(self, req, **kw):
    hub.spawn_after(0, self.do_exit)
    return "ok"

  @rest_command
  def get_clear(self, req, **kw):
    Tipsy._instance.clear_switch()
    return "ok"

  @staticmethod
  def do_exit():
    m = app_manager.AppManager.get_instance()
    m.uninstantiate('Tipsy')
    pid = os.getpid()
    os.kill(pid, signal.SIGTERM)

def handle_sigint(sig_num, stack_frame):
  url = 'http://%s:%s' % (wsgi_conf.wsapi_host, wsgi_conf.wsapi_port)
  url += '/tipsy/exit'
  hub.spawn_after(0, requests.get, url)
signal.signal(signal.SIGINT, handle_sigint)
