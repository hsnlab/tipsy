#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys

def byte_seq (template, seq):
  return template % (int(seq / 254), (seq % 254) + 1)

class PL (object):

  def __init__ (self, args):
    self.args = args
    self.components = ['base']
    self.conf = {}

  def create_conf (self):
    for c in self.components:
      method = getattr(self, 'add_%s' % c)
      method()
    return self.conf

  def add_base (self):
    self.conf['pipeline'] = args.pipeline
    self.conf['fakedrop'] = args.fakedrop
    self.conf['run_time'] = [] # Commands to be executed in every second

  def add_bsts (self):
    bsts = []
    for b in range(self.args.bst):
      bsts.append({
        'id': b,
        'mac': byte_seq('aa:cc:dd:cc:%02x:%02x', b),
        'ip': byte_seq('1.1.%d.%d', b),
        'port': None,
      })
    self.conf['bsts'] = bsts

  def add_servers (self):
    srvs = []
    for s in range(self.args.server):
      srvs.append({
        'ip': byte_seq('2.%d.%d.2', s),
        'nhop': s % self.args.nhop
      })
    self.conf['srvs'] = srvs

  def add_nhops (self):
    nhops = []
    for n in range(self.args.nhop):
      nhops.append({
        'dmac': byte_seq('aa:bb:bb:aa:%02x:%02x', n),
        'smac': byte_seq('ee:dd:dd:aa:%02x:%02x', n),
        'port': None,
      })
    self.conf['nhops'] = nhops

  def add_users (self):
    users = []
    for u in range(self.args.user):
      users.append({
        'ip': byte_seq('3.3.%d.%d', u),
        'tun_end': u % self.args.bst,
        'teid': u + 1,
        'rate_limit': self.args.rate_limit,
      })
    self.users = users
    self.conf['users'] = users

  def add_fw (self):
    ul_fw_rules = []
    dl_fw_rules = []
    for i in range(self.args.fw_rules):
      ul_fw_rules.append({
        'src_ip': '25.%d.1.1' % i,
        'dst_ip': '26.%d.2.2' % (200 - i),
        'src_port': 1000 + i,
        'dst_port': 1500 - i,
      })
      dl_fw_rules.append({
        'src_ip': '27.%d.1.1' % i,
        'dst_ip': '28.%d.2.2' % (200 - i),
        'src_port': 1000 + i,
        'dst_port': 1500 - i,
      })
    self.conf.update(
      {'ul_fw_rules': ul_fw_rules,
       'dl_fw_rules': dl_fw_rules,
      })

  def add_nat (self):
    # The 'nat' component depends on the 'users' component
    # NB: this requirement is not checked.
    nat = []
    pub_ip_format_str = '200.1.%d.%d'  # TODO: Make this configurable
    max_port = 65534
    for user in self.users:
      priv_ip = user['ip']
      for c in range(self.args.user_conn):
        priv_port = c + 1
        port_idx = (user['teid']-1) * self.args.user_conn + c
        pub_port = (port_idx % max_port) + 1
        incr = int((port_idx / max_port))
        nat.append({'priv_ip': priv_ip,
                    'priv_port': priv_port,
                    'pub_ip': byte_seq(pub_ip_format_str, incr),
                    'pub_port': pub_port,
                    'proto': 6, # TCP
        })
    self.conf['nat_table'] = nat

  def add_dcgw (self):
    self.conf['dcgw'] = {
      'vni': 666,
      'ip': '211.0.0.1',
      'mac':'ac:dc:AC:DC:ac:dc',
    }

  def add_gw (self):
    self.conf['gw'] = {
      'ip': self.args.gw_ip,
      'mac': self.args.gw_mac,
      'default_gw' : {'ip': self.args.downlink_default_gw_ip,
                      'mac': self.args.downlink_default_gw_mac,
      },
    }

  def add_fluct_user (self):
    # Generate extra users to fluctuate
    extra_users = []
    for u in range(self.args.fluct_user):
        extra_users.append({
            'ip': byte_seq('4.4.%d.%d', u),
            'tun_end': u % self.args.bst,
            'teid': u + self.args.user + 1,
            'rate_limit': self.args.rate_limit,
        })
    fl_u_add = []
    fl_u_del = []
    for i in range(self.args.fluct_user):
        user = extra_users[i]
        fl_u_add = fl_u_add + [{'action': 'add_user', 'args': user}]
        fl_u_del = [{'action': 'del_user', 'args': user}] + fl_u_del

    self.conf['run_time'] = fl_u_add + self.conf['run_time'] + fl_u_del

  def add_fluct_server (self):
    # Generate extra servers to fluctuate
    extra_servers = []
    for s in range(self.args.fluct_server):
        extra_servers.append({
            'ip': byte_seq('5.%d.%d.2', s),
            'nhop': s % self.args.nhop
        })
    fl_s_add = []
    fl_s_del = []
    for i in range(self.args.fluct_server):
        server = extra_servers[i]
        fl_s_add = fl_s_add + [{'action': 'add_server', 'args': server}]
        fl_s_del = [{'action': 'del_server', 'args': server}] + fl_s_del

    self.conf['run_time'] = fl_s_add + self.conf['run_time'] + fl_s_del


class PL_l3fwd (PL):
  "L3 Packet Forwarding pipeline"

  def __init__ (self, args):
    super(PL_l3fwd, self).__init__(args)
    self.components += ['l3']

  def add_l3 (self):
    for i, d in enumerate(['upstream', 'downstream']):
      table = []
      for s in range(self.args.__dict__['%s_l3_table_size' % d]):
        table.append({
          'ip': byte_seq('%d.%%d.%%d.2' % (2 + i), s),
          'nhop': s % self.args.__dict__['%s_group_table_size' % d]
        })
      self.conf['%s-l3-table' % d] = table

    for d in ['upstream', 'downstream']:
      table = []
      for n in range(self.args.__dict__['%s_group_table_size' %d]):
        dprefix = {'upstream': 'aa:bb:bb:aa',
                   'downstream': 'ab:ba:ab:ba'}[d]
        sprefix = {'upstream': 'ee:dd:dd:aa',
                   'downstream': 'ed:da:ed:da'}[d]
        table.append({
          'dmac': byte_seq('%s:%%02x:%%02x' % dprefix, n),
          'smac': byte_seq('%s:%%02x:%%02x' % sprefix, n),
          'port': None,
        })
      self.conf['%s-group-table' % d] = table

    add_rules = []
    del_rules = []
    for i, d in enumerate(['upstream', 'downstream']):
      extra_servers = []
      uts = self.args.upstream_l3_table_size
      dts = self.args.downstream_l3_table_size
      fluct = self.args.fluct_l3_table
      u_servers = int(fluct * uts / (uts + dts))
      servers = {'upstream': u_servers,
                 'downstream': (fluct - u_servers)}[d]
      for s in range(servers):
        extra_servers.append({
          'ip': byte_seq('%d.%%d.%%d.2' % (5 + i), s),
          'nhop': s % self.args.__dict__['%s_group_table_size' % d]
        })
      for server in extra_servers:
        add_rules = add_rules + [{'action': 'add_server', 'args': server}]
        del_rules = [{'action': 'del_server', 'args': server}] + del_rules
    self.conf['run_time'] = add_rules + self.conf['run_time'] + del_rules

    add_rules = []
    del_rules = []
    for i, d in enumerate(['upstream', 'downstream']):
      extra_nhops = []
      uts = self.args.upstream_group_table_size
      dts = self.args.downstream_group_table_size
      fluct = self.args.fluct_group_table
      u_nhops = int(fluct * uts / (uts + dts))
      nhops = {'upstream': u_nhops,
               'downstream': (fluct - u_nhops)}[d]
      for n in range(nhops):
        dprefix = {'upstream': 'aa:bb:bb:ff',
                   'downstream': 'ab:ba:ab:ff'}[d]
        sprefix = {'upstream': 'ee:dd:dd:ff',
                   'downstream': 'ed:da:ed:ff'}[d]
        extra_nhops.append({
          'dmac': byte_seq('%s:%%02x:%%02x' % dprefix, n),
          'smac': byte_seq('%s:%%02x:%%02x' % sprefix, n),
          'port': None,
        })
      for server in extra_nhops:
        add_rules = add_rules + [{'action': 'add_server', 'args': server}]
        del_rules = [{'action': 'del_server', 'args': server}] + del_rules
    self.conf['run_time'] = add_rules + self.conf['run_time'] + del_rules


class PL_mgw (PL):
  "Mobile Gateway"

  def __init__ (self, args):
    super(PL_mgw, self).__init__(args)
    self.components += ['gw', 'bsts', 'servers', 'nhops', 'users',
                        'handover', 'fluct_server', 'fluct_user']

  def add_handover (self):
    run_time = []
    for i in range(self.args.handover):
      # new_bst_id = (old_bst_id + bst_shift) % args.bst
      user = self.users[i % self.args.user]
      run_time.append({'action': 'handover',
                       'args': {
                         'user_teid': user['teid'],
                         'bst_shift': i + 1,
                       }})

    self.conf['run_time'] += run_time


class PL_vmgw (PL_mgw):
  "Virtual Mobile Gateway"

  def __init__ (self, args):
    super(PL_vmgw, self).__init__(args)
    self.components += ['dcgw', 'fw', 'apps']

  def add_apps (self):
    if self.args.napps != 1:
      raise NotImplementedError('Currently, one app is supported.')
    self.conf['napps'] = self.args.napps


class PL_bng (PL):
  "Broadband Network Gateway"

  def __init__ (self, args):
    super(PL_bng, self).__init__(args)
    self.components += ['fw', 'cpe', 'gw', 'users', 'nat',
                        'servers', 'nhops', 'fluct_server', 'fluct_user']


  def add_cpe (self):
    cpe = []
    for b in range(self.args.cpe):
      cpe.append({
        'id': b,
        'mac': byte_seq('aa:cc:dd:cc:%02x:%02x', b),
        'ip': byte_seq('1.1.%d.%d', b),
        'port': None,
      })
    self.conf['cpe'] = cpe

  def add_users (self):
    users = []
    for u in range(self.args.user):
      users.append({
        'ip': byte_seq('3.3.%d.%d', u),
        'tun_end': u % self.args.cpe,
        'teid': u + 1,
        'rate_limit': self.args.rate_limit,
      })
    self.users = users
    self.conf['users'] = users


def list_pipelines ():
  l = [n[3:] for n in globals() if n.startswith('PL_')]
  return sorted(l)

def check_type_positive_integer (string):
  msg = "'%s' is not a positive integer" % string
  try:
    i = int(string)
  except ValueError:
    raise argparse.ArgumentTypeError(msg)
  if i <= 0:
    raise argparse.ArgumentTypeError(msg)
  return i

def check_type_non_negative_integer (string):
  msg = "'%s' is not non-negative integer" % string
  try:
    i = int(string)
  except ValueError:
    raise argparse.ArgumentTypeError(msg)
  if i < 0:
    raise argparse.ArgumentTypeError(msg)
  return i

def check_type_ip_address (string):
  msg = "'%s' is not an ip address" % string
  l = string.split('.')
  if len(l) != 4:
    raise argparse.ArgumentTypeError(msg)
  for i in l:
    if int(i) > 255 or int(i) < 0:
      raise argparse.ArgumentTypeError(msg)
  return string

def check_type_mac_address (string):
  if re.match(r'^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$', string):
    return string
  msg = "'%s' is not a mac address" % string
  raise argparse.ArgumentTypeError(msg)

def add_args_from_schema(parser, pipeline_name):
  "Add per pipeline CLI args from the schema definition"
  type_map = {
    'string': str,
    'boolean': bool,
  }

  group = parser.add_argument_group('pipeline specific agruments')
  script_dir = os.path.dirname(os.path.realpath(__file__))
  fname = 'pipeline-%s.json' % pipeline_name
  with open(os.path.join(script_dir, fname)) as f:
    schema = json.load(f)

  name = schema['properties']['pipeline']['enum'][0]
  for prop, val in sorted(schema['properties'].items()):
    #print(prop, val)
    if val.get('$ref'):
      m = re.search(r'#\/(.*)$', val['$ref'])
      fn = re.sub(r'-', '_', m.group(1))
      type_check_fn = globals()['check_type_%s' % fn]
    else:
      type_check_fn = type_map[val['type']]
    a = {'type': type_check_fn,
         'help': val['description'],
    }
    if 'default' in val:
      a['default'] = val['default']
    #print(a)
    if prop != 'pipeline':
      parser.add_argument('--%s' % prop, **a)


parser = argparse.ArgumentParser()
parser2 = argparse.ArgumentParser()
for args, kw in [
    (['--json', '-j'], {
      'type': argparse.FileType('r'),
      'help': 'Override default settings from config file'}),
    (['--output', '-o'], {
      'type': argparse.FileType('w'),
      'help': 'Output file',
      'default': '/dev/stdout'}),
    (['--pipeline', '-p'], {
      'type': str,
      'choices': list_pipelines(),
      'help': 'Name of the pipeline',
      'default': 'mgw'}),
    (['--info', '-i'], {
      'action': 'store_true',
      'help': 'Show detailed info of a pipeline and exit'}),
]:
  parser.add_argument(*args, **kw)
  parser2.add_argument(*args, **kw)

parser.set_defaults(info=False)
parser2.add_argument('dummy', metavar='pipeline specific args ...',
                    nargs='?', type=str, help='see -i for details')

(args, _) = parser2.parse_known_args()
if args.json:
  # Set the default pipeline from the json file
  new_defaults = json.load(args.json)
  parser.set_defaults(**new_defaults)
  (args, _) = parser.parse_known_args()

add_args_from_schema(parser, args.pipeline)
if args.json:
  # Override the defaults for the given pipeline
  parser.set_defaults(**new_defaults)
args = parser.parse_args()

if args.info:
  parser = argparse.ArgumentParser()
  parser.formatter_class = argparse.ArgumentDefaultsHelpFormatter
  pl = globals()['PL_%s' % args.pipeline]({})
  parser.usage = "\n\n%s (%s)"  % (pl.__doc__, args.pipeline)
  add_args_from_schema(parser, args.pipeline)
  parser.parse_args(['-h'])
else:
  pl = globals()['PL_%s' % args.pipeline](args)
  conf = pl.create_conf()
  json.dump(conf, args.output, sort_keys=True, indent=4)
  args.output.write("\n")
