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
from ryu.ofproto import ofproto_v1_3
from ryu.services.protocols.bgp.utils.evtlet import LoopingCall

import ip
import sw_conf_vsctl as sw_conf

fdir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(fdir, '..', 'lib'))
import find_mod

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


class Tipsy(app_manager.RyuApp):
  OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
  _CONTEXTS = { 'wsgi': WSGIApplication }
  _instance = None

  def __init__(self, *args, **kwargs):
    super(Tipsy, self).__init__(*args, **kwargs)
    Tipsy._instance = self
    self.logger.debug(" __init__()")

    self.switch_name = 'ovs'
    self.result = {}
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
      backend = 'SUT_%s' % self.switch_name
      self.logger.warn('backend: %s', backend)
      pl_class = find_mod.find_class(backend, self.pl_conf.name)
    except KeyError as e:
      pl_class = None
      self.logger.error('keyerror: %s', e)
    if pl_class:
      self.pl = pl_class(self, self.pl_conf)
      return

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
    sw_conf.set_coremask(self.bm_conf.sut.coremask)
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
    sw_conf.set_coremask(self.bm_conf.sut.coremask)
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
    for field in ['mfr_desc', 'hw_desc', 'sw_desc', 'serial_num', 'dp_desc']:
      self.result[field] = getattr(ev.msg.body, field)

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
