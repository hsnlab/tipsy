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
"""

import datetime
import importlib
import json
import os
import re
import requests
import signal
import subprocess
import sys
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

import exp_ericsson as eri
import sw_conf_erfs as sw_conf
print('main',len(dir(ofproto_v1_3)))
eri.register(ofproto_v1_3)

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
    self.__dict__.update({k.replace('-', '_'): v for k, v in kwargs.items()})

  def __repr__(self):
    return self.__dict__.__repr__()

  def __getattr__(self, name):
    return self.__dict__[name.replace('-', '_')]

  def __setattr__(self, name, val):
    self.__dict__[name.replace('-', '_')] = val

  def get (self, attr, default=None):
    return self.__dict__.get(attr.replace('-', '_'), default)


class PL(object):
  def __init__(self, parent, conf):
    self.conf = conf
    self.parent = parent
    self.logger = self.parent.logger
    self.tables = {'drop': 0}

  def get_tunnel_endpoints(self):
    raise NotImplementedError

  def do_unknown(self, action):
    self.logger.error('Unknown action: %s' % action.action)


class PL_mgw(PL):

  def __init__(self, parent, conf):
    super(PL_mgw, self).__init__(parent, conf)
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

  def mod_server(self, cmd, srv):
    self.logger.debug('%s-server: ip=%s' % (cmd, srv.ip))
    parser = self.parent.dp.ofproto_parser
    match = {'eth_type': ETH_TYPE_IP, 'ipv4_dst': srv.ip}
    action = parser.OFPActionGroup(srv.nhop)
    self.parent.mod_flow('l3_lookup', None, match, [action], cmd=cmd)

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

    self.result = {}
    self.lock = False
    self.dp_id = None
    self.configured = False
    self.dl_port = None # port numbers in the OpenFlow switch
    self.ul_port = None #
    self.status = 'init'
    self.core_idx = 0 # next core to allocate in the core_list

    self.logger.debug("%s, %s" % (args, kwargs))

    self.parse_conf('pl_conf', CONF['pipeline_conf'])
    self.parse_conf('bm_conf', CONF['benchmark_conf'])
    # port "names" used to configure the OpenFlow switch
    self.dl_port_name = self.bm_conf.sut.downlink_port
    self.ul_port_name = self.bm_conf.sut.uplink_port
    self.instantiate_pipeline()

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

  def instantiate_pipeline(self):
    sys.path.append(os.path.dirname(os.path.realpath(__file__)))
    try:
      module = importlib.import_module('pipeline.%s' % self.pl_conf.name)
      pl_class = getattr(module, 'PL')
      self.pl = pl_class(self, self.pl_conf)
      return
    except ImportError as e:
      self.logger.warn('Failed to import pipeine: %s' % e)

    try:
      self.pl = globals()['PL_%s' % self.pl_conf.name](self, self.pl_conf)
    except (KeyError, NameError) as e:
      self.logger.error('Failed to instanciate pipeline (%s): %s' %
                        (self.pl_conf.name, e))
      raise(e)

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
    sw_conf.add_port(br_name, port_name, iface, core)

  def get_cores(self, num_cores):
    coremask = self.bm_conf.sut.coremask
    cpum = int(coremask, 16)
    core_list = [i for i in range(32) if (cpum >> i) & 1 == 1]
    core_list = [i for i in core_list if i != 0] # lcore 0 is reserved for the controller
    cores = []
    for i in range(num_cores):
      cores.append(core_list[self.core_idx])
      self.core_idx = (self.core_idx + 1) % len(core_list)
    return cores

  def initialize_datapath(self):
    self.change_status('start_erfs')

    coremask = self.bm_conf.sut.coremask
    os.system('pkill dof')
    #cmd = ['./dof', '-c', coremask, '--socket-mem=1024,1024', '--', '-d', '10']
    cmd = ['./dof', '-c', coremask, '--', '-d', '10']
    cwd = self.bm_conf.sut.erfs_dir
    subprocess.Popen(cmd, cwd=cwd)
    time.sleep(15)

    self.change_status('initialize_datapath')
    sw_conf.init_sw()

    br_num = 1 # 'br-main'
    sw_conf.add_bridge(br_num)

    core = self.get_cores(self.bm_conf.pipeline.core)
    self.add_port(br_num, 1, self.ul_port_name, core=core)
    self.add_port(br_num, 2, self.dl_port_name, core=core)
    self.ul_port = 1
    self.dl_port = 2

    hub.spawn_after(1, sw_conf.set_controller, br_num, '127.0.0.1')

  def stop_datapath(self):
    sw_conf.del_bridge(1)

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
    for field in ['mfr_desc', 'hw_desc', 'sw_desc', 'serial_num', 'dp_desc']:
      self.result[field] = getattr(ev.msg.body, field)

  @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
  def handle_port_desc_stats_reply(self, ev):
    ofp = self.dp.ofproto

    self.ports = {}
    for port in ev.msg.body:
      self.ports[port.name] = port.port_no
    for name in sorted(self.ports):
      self.logger.debug('port: %s, %s' % (name, self.ports[name]))
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
    else:
      mod_flow('drop', match={'in_port': self.ul_port}, output=self.dl_port)
      mod_flow('drop', match={'in_port': self.dl_port}, output=self.ul_port)

  def configure(self):
    if self.configured:
      return

    ofp = self.dp.ofproto
    parser = self.dp.ofproto_parser

    # for bst in self.pl_conf.get('bsts', []):
    #   self.add_vxlan_tun('tun', bst)
    # for cpe in self.pl_conf.get('cpe', []):
    #   self.add_vxlan_tun('tun', cpe)

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
  def get_result(self, req, **kw):
    return Tipsy._instance.result

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
