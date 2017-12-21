# Copyright (C) 2017 Felician Nemeth, nemethf@tmit.bme.hu
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
TIPSY: Simplified mobile gateway

Run as:
    $ ryu-manager --log-config-file path/to/log.cfg path/to/tipsy.py
or:
    $ cd path/to/tipsy.py
    $ ryu-manager --config-dir .

The setup is similar to the left side of the figure in
http://docs.openvswitch.org/en/latest/howto/userspace-tunneling/

# ovsdb
~/ryu$ cat ryu/doc/source/library_ovsdb_manager.rst
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
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER
from ryu.controller.handler import HANDSHAKE_DISPATCHER
from ryu.controller.handler import MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub
from ryu.lib import ofctl_utils as ofctl
from ryu.lib.packet import in_proto
from ryu.lib.packet.ether_types import ETH_TYPE_IP
from ryu.ofproto import ofproto_v1_3
from ryu.services.protocols.bgp.utils.evtlet import LoopingCall

import ip
import sw_conf_vsctl as sw_conf

TABLES = {
  'ingress'   : 0,
  'uplink'    : 2,
  'ul_fw'     : 3,
  'dl_fw'     : 4,
  'downlink'  : 5,
  'l3_lookup' : 6,
  'drop'      : 250
}

conf_file = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                        'conf.json')
cfg.CONF.register_opts([
  cfg.StrOpt('conf_file', default=conf_file,
             help='json formatted configuration file of the TIPSY measurment'),

  # in_port means ofp.OFPP_IN_PORT, i.e., send to where it came from
  # downlink: towards the base-stations and user equipments.
  # uplink  : towards the servers (internet) via next-hop routers.
  cfg.StrOpt('dl_port', default='in_port',
             help='name of the downlink port (default: in_port)'),
  cfg.StrOpt('ul_port', default='in_port',
             help='name of the downlink port (default: in_port)'),

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

  def get (self, attr, default=None):
    return self.__dict__.get(attr, default)


class Tipsy(app_manager.RyuApp):
  OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
  _CONTEXTS = { 'wsgi': WSGIApplication }
  _instance = None

  def __init__(self, *args, **kwargs):
    super(Tipsy, self).__init__(*args, **kwargs)
    Tipsy._instance = self
    self.logger.debug(" __init__()")

    self.conf_file = CONF['conf_file']
    self.lock = False
    self.dp_id = None
    self.configured = False
    self.dl_port = None
    self.ul_port = None
    self.status = 'init'

    self.logger.debug("%s, %s" % (args, kwargs))
    self.logger.info("conf_file: %s" % self.conf_file)

    try:
      with open(self.conf_file, 'r') as f:
        conv_fn = lambda d: ObjectView(**d)
        self.pl_conf = json.load(f, object_hook=conv_fn)
    except IOError as e:
      self.logger.error('Failed to load cfg file (%s): %s' %
                        (self.conf_file, e))
      raise(e)
    except ValueError as e:
      self.logger.error('Failed to parse cfg file (%s): %s' %
                        (self.conf_file, e))
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
      attr = getattr(self, 'do_%s' % cmd.action, self.do_unknown)
      attr(cmd)

    #time.sleep(0.5)
    self.logger.warn("time      :  %s",  datetime.datetime.now())

    self.lock = False

  def add_port(self, br_name, port_name, iface):
    """Add a new port to an ovs bridge.
    iface can be a PCI address (type => dpdk), or
    a kernel interface name (type => system)
    """
    # We could be smarter here, but this will do
    if iface.find(':') > 0:
      sw_conf.add_port(br_name, port_name, type='dpdk',
                       options={'dpdk-devargs': iface})
    else:
      sw_conf.add_port(br_name, port_name, type='system', name=iface)

  def initialize_datapath(self):
    self.change_status('initialize_datapath')

    br_name = 'br-phy'
    sw_conf.del_bridge(br_name, can_fail=False)
    sw_conf.add_bridge(br_name, hwaddr=self.pl_conf.gw.mac, dp_desc=br_name)
    sw_conf.set_datapath_type(br_name, 'netdev')
    self.add_port(br_name, 'dl', CONF['dl_port'])
    ip.set_up(br_name, self.pl_conf.gw.ip + '/24')

    br_name = 'br-int'
    sw_conf.del_bridge(br_name, can_fail=False)
    sw_conf.add_bridge(br_name, dp_desc=br_name)
    sw_conf.set_datapath_type(br_name, 'netdev')
    sw_conf.set_controller(br_name, 'tcp:127.0.0.1')
    sw_conf.set_fail_mode(br_name, 'secure')
    self.add_port(br_name, 'ul', CONF['ul_port'])

    ip.add_veth('veth-phy', 'veth-int')
    ip.set_up('veth-int')
    ip.set_up('veth-phy')
    sw_conf.add_port('br-int', 'veth-int', type='system')
    sw_conf.add_port('br-phy', 'veth-phy', type='system')
    # Don't use a controller for the following static rules
    cmd = 'sudo ovs-ofctl --protocol OpenFlow13 add-flow br-phy priority=1,'
    in_out = [('veth-phy', 'dl'), ('dl', 'br-phy'), ('br-phy', 'dl')]
    for in_port, out_port in in_out:
      cmd_tail = 'in_port=%s,actions=output:%s' % (in_port, out_port)
      if subprocess.call(cmd + cmd_tail, shell=True):
        self.logger.error('cmd failed: %s' % cmd)

    nets = {}
    for bst in self.mgw_conf.bsts:
      net = re.sub(r'[.][0-9]+$', '.0/24', bst.ip)
      nets[str(net)] = True
    for net in nets.iterkeys():
      ip.add_route_gw(net, self.pl_conf.gw.default_gw.ip)
    self.set_arp_table()

    self.change_status('wait')  # Wait datapath to connect

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

    # Map port names in cfg to actual port numbers
    if CONF['dl_port'] == CONF['ul_port']:
      CONF['dl_port'] = CONF['ul_port'] = 'in_port'
    self.ports = {'in_port': ofp.OFPP_IN_PORT}
    for port in ev.msg.body:
      self.ports[port.name] = port.port_no
    for name in sorted(self.ports):
      self.logger.debug('port: %s, %s' % (name, self.ports[name]))

    for port_type in ['ul_port']:
      port_name = CONF[port_type]
      if self.ports.get(port_name):
        port_no = self.ports[port_name]
        self.__dict__[port_type] = port_no
        self.logger.info('%s (%s): %s' % (port_type, port_name, port_no))
      else:
        self.logger.critical('%s (%s): not found' % (port_type, port_name))
    self.configure_1()

  @set_ev_cls(ofp_event.EventOFPErrorMsg,
              [HANDSHAKE_DISPATCHER, CONFIG_DISPATCHER, MAIN_DISPATCHER])
  def handle_error_msg(self, ev):
    msg = ev.msg
    ofp = self.dp.ofproto

    if msg.type == ofp.OFPET_METER_MOD_FAILED:
      cmd = 'ovs-vsctl set bridge s1 datapath_type=netdev'
      self.logger.error('METER_MOD failed, "%s" might help' % cmd)
    else:
      self.logger.error('OFPErrorMsg received: type=0x%02x code=0x%02x '
                        'message=%s',
                        msg.type, msg.code, utils.hex_array(msg.data))

  def goto(self, table_name):
    "Return a goto insturction to table_name"
    parser = self.dp.ofproto_parser
    return parser.OFPInstructionGotoTable(TABLES[table_name])

  def get_bst_port(self, bst_id):
    port_name = 'bst-%s' % bst_id
    return self.ports[port_name]

  def mod_flow(self, table=0, priority=None, match=None,
               actions=[], inst=[], out_port=None, out_group=None,
               cmd='add'):

    ofp = self.dp.ofproto
    parser = self.dp.ofproto_parser

    if type(table) == str:
      table = TABLES[table]
    if priority is None:
      priority = ofp.OFP_DEFAULT_PRIORITY
    if cmd == 'add':
      command=ofp.OFPFC_ADD
    elif cmd == 'del':
      command=ofp.OFPFC_DELETE
    else:
      command=cmd

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

  def mod_user(self, cmd='add', user=None):
    self.logger.debug('%s-user: teid=%d' % (cmd, user.teid))
    ofp = self.dp.ofproto
    parser = self.dp.ofproto_parser

    if user.teid == 0:
      # meter_id = teid, and meter_id cannot be 0
      self.logger.warn('Skipping user (teid==0)')
      return

    # Create per user meter
    command = {'add': ofp.OFPMC_ADD, 'del': ofp.OFPMC_DELETE}[cmd]
    band = parser.OFPMeterBandDrop(rate=user.rate_limit/1000) # kbps
    msg = parser.OFPMeterMod(self.dp, command=command,
                             meter_id=user.teid, bands=[band])
    self.dp.send_msg(msg)

    # Uplink: vxlan_port -> rate-limiter -> (FW->NAT) -> L3 lookup table
    if self.pl_conf.pipeline in ['bng']:
      next_tbl = 'ul_fw'
    else:
      next_tbl = 'l3_lookup'
    match = parser.OFPMatch(tunnel_id=user.teid)
    inst = [parser.OFPInstructionMeter(meter_id=user.teid), self.goto(next_tbl)]
    self.mod_flow('uplink', match=match, inst=inst, cmd=cmd)

    # Downlink: (NAT->FW) -> rate-limiter -> vxlan_port
    match = parser.OFPMatch(eth_type=ETH_TYPE_IP,
                            ipv4_dst=user.ip)
    out_port = self.get_tun_port(user.tun_end)
    inst = [parser.OFPInstructionMeter(meter_id=user.teid)]
    actions = [parser.OFPActionSetField(tunnel_id=user.teid),
               parser.OFPActionOutput(out_port)]
    self.mod_flow('downlink', match=match, actions=actions, inst=inst, cmd=cmd)

  def mod_server(self, cmd, srv):
    self.logger.debug('%s-server: ip=%s' % (cmd, srv.ip))
    parser = self.dp.ofproto_parser
    match = parser.OFPMatch(eth_type=ETH_TYPE_IP, ipv4_dst=srv.ip)
    action = parser.OFPActionGroup(srv.nhop)
    self.mod_flow('l3_lookup', None, match, [action], cmd=cmd)

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

  def add_fw_rules(self, table_name, rules, next_table):
    if not rules:
      return

    parser = self.dp.ofproto_parser
    inst = [self.goto('drop')]
    for rule in rules:
      # TODO: ip_proto, ip mask, port mask (?)
      match = parser.OFPMatch(
        eth_type=ETH_TYPE_IP,
        ip_proto=in_proto.IPPROTO_TCP,
        ipv4_src=(rule.src_ip, '255.255.255.0'),
        ipv4_dst=(rule.dst_ip, '255.255.255.0'),
        tcp_src=rule.src_port,
        tcp_dst=rule.dst_port)
      self.mod_flow(table_name, match=match, inst=inst)
    self.mod_flow(table_name, priority=1, inst=[self.goto(next_table)])

  def configure_ingress(self):
    parser = self.dp.ofproto_parser

    match = parser.OFPMatch(in_port=self.ports['veth-int'])
    self.mod_flow('ingress', 9, match, [], [])
    match = parser.OFPMatch(in_port=self.ul_port,
                            eth_dst=self.pl_conf.gw.mac)
    if self.pl_conf.pipeline in ['bng']:
      next_table='dl_fw'
    else:
      next_table='downlink'
    self.mod_flow('ingress', 9, match, [], [self.goto(next_table)])
    match = parser.OFPMatch(in_port=self.ul_port)
    self.mod_flow('ingress', 8, match, [], [self.goto('drop')])
    self.mod_flow('ingress', 7, None, [], [self.goto('uplink')])

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
    for table_id in TABLES.values():
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

  def configure(self):
    if self.configured:
      return

    ofp = self.dp.ofproto
    parser = self.dp.ofproto_parser
    self.clear_switch()

    for bst in self.mgw_conf.bsts:
      sw_conf.add_port(self.dp_id,
                       'bst-%s' % bst.id,
                       type='vxlan',
                       options={'key': 'flow',
                                'remote_ip': bst.ip})

    self.dp.send_msg(parser.OFPPortDescStatsRequest(self.dp, 0, ofp.OFPP_ANY))
    self.change_status('wait_for_PortDesc')
    # Will continue from self.configure_1()

  def configure_1(self):
    self.change_status('configure_1')
    ofp = self.dp.ofproto
    parser = self.dp.ofproto_parser

    self.configure_ingress()

    # Insert default drop actions for the sake of statistics
    for table_name in TABLES.iterkeys():
      if table_name != 'drop':
        self.mod_flow(table_name, 0, inst=[self.goto('drop')])
    if not self.pl_conf.fakedrop:
      self.mod_flow('drop', 0)
    else:
      output = parser.OFPActionOutput
      match = parser.OFPMatch(in_port=self.ul_port)
      port = self.ports['veth-int']
      self.mod_flow('drop', 1, match=match, actions=[output(port)])
      self.mod_flow('drop', 0, actions=[output(self.ul_port)])

    for user in self.pl_conf.users:
      self.mod_user('add', user)

    self.add_fw_rules('ul_fw', self.mgw_conf.ul_fw_rules, 'l3_lookup')
    self.add_fw_rules('dl_fw', self.mgw_conf.dl_fw_rules, 'downlink')

    for i, nhop in enumerate(self.pl_conf.nhops):
      out_port = nhop.port or self.ul_port
      set_field = parser.OFPActionSetField
      self.add_group(i, [set_field(eth_dst=nhop.dmac),
                         set_field(eth_src=nhop.smac),
                         parser.OFPActionOutput(out_port)])

    for srv in self.pl_conf.srvs:
      self.mod_server('add', srv)

    # Finally, send and wait for a barrier
    parser = self.dp.ofproto_parser
    msg = parser.OFPBarrierRequest(self.dp)
    msgs = []
    ofctl.send_stats_request(self.dp, msg, self.waiters, msgs, self.logger)

    self.handle_configured()

  def handle_configured(self):
    "Called when initial configuration is uploaded to the switch"

    self.configured = True
    self.change_status('configured')
    try:
      requests.post(CONF['webhook_configured'], data='')
    except requests.ConnectionError:
      pass
    if self.pl_conf.get('run_time'):
      self._timer.start(1)
    # else:
    #   hub.spawn_after(1, TipsyController.do_exit)

  def do_handover(self, action):
    parser = self.dp.ofproto_parser
    log = self.logger.debug
    user_idx= action.args.user_teid - 1
    user = self.pl_conf.users[user_idx]
    old_bst = user.bst
    new_bst = (user.bst + action.args.bst_shift) % len(self.pl_conf.bsts)
    log("handover user.%s: bst.%s -> bst.%s" % (user.teid, old_bst, new_bst))
    user.bst = new_bst
    self.pl_conf.users[user_idx] = user

    # Downlink: rate-limiter -> vxlan_port
    match = parser.OFPMatch(eth_type=ETH_TYPE_IP,
                            ipv4_dst=user.ip)
    out_port = self.get_tun_port(new_bst)
    actions = [parser.OFPActionSetField(tunnel_id=user.teid),
               parser.OFPActionOutput(out_port)]
    inst = [parser.OFPInstructionMeter(meter_id=user.teid)]
    self.mod_flow('downlink', match=match, actions=actions, inst=inst, cmd='add')

  def do_add_user(self, action):
    self.mod_user('add', action.args)

  def do_del_user(self, action):
    self.mod_user('del', action.args)

  def do_add_server(self, action):
    self.mod_server('add', action.args)

  def do_del_server(self, action):
    self.mod_server('del', action.args)

  def do_unknown(self, action):
    self.logger.error('Unknown action: %s' % action.action)


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
    hub.spawn_after(1, self.do_exit)
    return "ok"

  @rest_command
  def get_clear(self, req, **kw):
    Tipsy._instance.clear_switch()
    return "ok"

  @staticmethod
  def do_exit():
    tipsy = Tipsy._instance
    tipsy.change_status('shutdown')
    tipsy.close()
    m = app_manager.AppManager.get_instance()
    m.uninstantiate('Tipsy')
    pid = os.getpid()
    os.kill(pid, signal.SIGTERM)

