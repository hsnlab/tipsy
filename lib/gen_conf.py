#!/usr/bin/env python3

# TIPSY: Telco pIPeline benchmarking SYstem
#
# Copyright (C) 2018 by it's authors (See AUTHORS)
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

import argparse
import json

import args_from_schema

__all__ = ["gen_conf"]

def gen_conf (args):
  parser = argparse.ArgumentParser()
  add_args_from_schema(parser, args.get('name', 'mgw'))
  parser.set_defaults(**args)
  args = parser.parse_args([])

  pl = globals()['PL_%s' % args.name](args)
  conf = pl.create_conf()
  return conf

def byte_seq (template, seq):
  return template % (int(seq / 254), (seq % 254) + 1)


class PL (object):

  def __init__ (self, args):
    self.args = args
    self.components = ['base']
    self.conf = {}

  def get_arg (self, arg_name, default=None):
    return self.args.__dict__.get(arg_name, default)

  def create_conf (self):
    for c in self.components:
      method = getattr(self, 'add_%s' % c)
      method()
    return self.conf

  def add_base (self):
    self.conf['name'] = self.args.name
    self.conf['fakedrop'] = self.args.fakedrop
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
    self.conf['srvs'] = self.create_l3_table(
      self.args.server, self.args.nhop, '2.%d.%d.2')

  def add_nhops (self):
    self.conf['nhops'] = self.create_l2_table(
      self.args.nhop, 'aa:bb:bb:aa:%02x:%02x', 'ee:dd:dd:aa:%02x:%02x')

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
    ul_fw_rules, dl_fw_rules = [], []
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

  def add_sut_mac_addresses (self):
    addr = {'ul_port_mac': self.args.uplink_mac,
            'dl_port_mac': self.args.downlink_mac}
    self.conf['sut'] = self.conf.get('sut', {})
    self.conf['sut'].update(addr)

  def add_fluct_user (self):
    # Generate ephemeral users
    extra_users = []
    for u in range(self.args.fluct_user):
        extra_users.append({
            'ip': byte_seq('4.4.%d.%d', u),
            'tun_end': u % self.args.bst,
            'teid': u + self.args.user + 1,
            'rate_limit': self.args.rate_limit,
        })
    fl_u_add, fl_u_del = [], []
    for i in range(self.args.fluct_user):
        user = extra_users[i]
        fl_u_add = fl_u_add + [{'action': 'add_user', 'args': user}]
        fl_u_del = [{'action': 'del_user', 'args': user}] + fl_u_del

    self.conf['run_time'] = fl_u_add + self.conf['run_time'] + fl_u_del

  def add_fluct_server (self):
    # Generate ephemeral servers
    extra_servers = self.create_l3_table(
      self.args.fluct_server, self.args.nhop, '5.%d.%d.2')
    fl_s_add, fl_s_del = [], []
    for server in extra_servers:
        fl_s_add = fl_s_add + [{'action': 'add_server', 'args': server}]
        fl_s_del = [{'action': 'del_server', 'args': server}] + fl_s_del

    self.conf['run_time'] = fl_s_add + self.conf['run_time'] + fl_s_del

  def create_l3_table (self, size, nhops, addr_template):
    table = []
    for i in range(size):
      table.append({
        'ip': byte_seq(addr_template, i),
        'prefix_len': 24,       # TODO: should vary
        'nhop': i % nhops
      })
    return table

  def create_l2_table (self, size, dmac_template, smac_template):
    nhops = []
    for n in range(size):
      nhops.append({
        'dmac': byte_seq(dmac_template, n),
        'smac': byte_seq(smac_template, n),
        'port': None,
      })
    return nhops

class PL_portfwd (PL):
  "L2 Port Forwarding pipeline"
  def __init__ (self, args):
    super().__init__(args)
    self.components = ['portfwd']

  def add_portfwd (self):
    self.conf['name'] = args.name
    self.conf['run_time'] = []
    for arg in ['name', 'mac_swap_upstream', 'mac_swap_downstream']:
      self.conf[arg] = self.get_arg(arg)


class PL_l2fwd (PL):
  "L2 Packet Forwarding pipeline"

  def __init__ (self, args):
    super().__init__(args)
    self.components += ['l2']

  def add_l2 (self):
    def make_tbl (size, template):
      table = []
      for i in range(size):
        table.append({'mac':  byte_seq(template, i), 'out_port': None})
      return table

    usize = self.args.upstream_table_size
    dsize = self.args.downstream_table_size
    self.conf['upstream-table'] = make_tbl(usize, 'aa:cc:dd:cc:%02x:%02x')
    self.conf['downstream-table'] = make_tbl(dsize, 'ac:dc:ac:dc:%02x:%02x')

    # Run-time behaviour, distribute dynamic entries proportionally
    fluct = self.args.fluct_table
    u_entries = int(fluct * usize / (usize + dsize))
    d_entries = fluct - u_entries

    def make_rule (op, entry, tbl):
      return [{
        'action': 'mod_table',
        'cmd': op,
        'entry': entry,
        'table': tbl,
      }]
    rules = []
    directions = ['upstream'] * u_entries + ['downstream'] * d_entries
    entries = make_tbl(fluct, 'aa:bb:bb:aa:%02x:%02x')
    for entry, d in zip(entries, directions):
      rules = make_rule('add', entry, d) + rules + make_rule('del', entry, d)
    self.conf['run_time'] += rules


class PL_l3fwd (PL):
  "L3 Packet Forwarding pipeline"

  def __init__ (self, args):
    super().__init__(args)
    self.components += ['l3', 'sut_mac_addresses']

  def add_l3 (self):
    for i, d in enumerate(['upstream', 'downstream']):
      self.conf['%s_l3_table' % d] = self.create_l3_table(
        size=self.get_arg('%s_l3_table_size' % d),
        nhops=self.get_arg('%s_group_table_size' % d),
        addr_template='%d.%%d.%%d.2' % (2 + i)
      )

    for d in ['upstream', 'downstream']:
      dprefix = {'upstream': 'aa:bb:bb:aa', 'downstream': 'ab:ba:ab:ba'}[d]
      sprefix = {'upstream': 'ee:dd:dd:aa', 'downstream': 'ed:da:ed:da'}[d]
      self.conf['%s_group_table' % d] = self.create_l2_table(
        size=self.get_arg('%s_group_table_size' %d),
        dmac_template='%s:%%02x:%%02x' % dprefix,
        smac_template='%s:%%02x:%%02x' % sprefix
      )

    # Define the run-time behaviour
    def make_rule(op, entry, tbl):
      return {'action': action, 'cmd': op, 'entry': entry, 'table': tbl}
    action = 'mod_l3_table'
    add_rules, del_rules  = [], []
    for i, d in enumerate(['upstream', 'downstream']):
      uts = self.args.upstream_l3_table_size
      dts = self.args.downstream_l3_table_size
      fluct = self.args.fluct_l3_table
      u_servers = int(fluct * uts / (uts + dts))
      size = {'upstream': u_servers, 'downstream': (fluct - u_servers)}[d]
      temp_table = self.create_l3_table(
        size=size,
        nhops=self.get_arg('%s_group_table_size' % d),
        addr_template='%d.%%d.%%d.2' % (5 + i)
      )
      for entry in temp_table:
        add_rules = add_rules + [make_rule('add', entry, d)]
        del_rules = [make_rule('del', entry, d)] + del_rules
    self.conf['run_time'] = add_rules + self.conf['run_time'] + del_rules

    action = 'mod_group_table'
    add_rules, del_rules = [], []
    for d in ['upstream', 'downstream']:
      uts = self.args.upstream_group_table_size
      dts = self.args.downstream_group_table_size
      fluct = self.args.fluct_group_table
      u_nhops = int(fluct * uts / (uts + dts))
      dprefix = {'upstream': 'aa:bb:bb:ff', 'downstream': 'ab:ba:ab:ff'}[d]
      sprefix = {'upstream': 'ee:dd:dd:ff', 'downstream': 'ed:da:ed:ff'}[d]
      extra_nhops = self.create_l2_table(
        size={'upstream': u_nhops, 'downstream': (fluct - u_nhops)}[d],
        dmac_template='%s:%%02x:%%02x' % dprefix,
        smac_template='%s:%%02x:%%02x' % sprefix
      )
      for entry in extra_nhops:
        add_rules = add_rules + [make_rule('add', entry, d)]
        del_rules = [make_rule('del', entry, d)] + del_rules
    self.conf['run_time'] = add_rules + self.conf['run_time'] + del_rules


class PL_mgw (PL):
  "Mobile Gateway"

  def __init__ (self, args):
    super().__init__(args)
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
    super().__init__(args)
    self.components += ['dcgw', 'fw', 'apps']

  def add_apps (self):
    if self.args.napps != 1:
      raise NotImplementedError('Currently, one app is supported.')
    self.conf['napps'] = self.args.napps


class PL_bng (PL):
  "Broadband Network Gateway"

  def __init__ (self, args):
    super().__init__(args)
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

def add_args_from_schema(parser, pipeline_name):
  "Add per pipeline CLI args from the schema definition"

  group = parser.add_argument_group('pipeline specific agruments')
  schema_name = 'pipeline-%s' % pipeline_name
  args_from_schema.add_args(parser, schema_name, ignored_properties=['name'])

def parse_cli_args ():
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
      'dest': 'name',
      'type': str,
      'choices': list_pipelines(),
      'help': 'Name of the pipeline',
      'default': 'mgw'}),
    (['--name'], {
      'dest': 'name',
      'type': str,
      'choices': list_pipelines(),
      'help': argparse.SUPPRESS,
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

  args, _ = parser2.parse_known_args()
  if args.json:
    # Set the default pipeline from the json file
    new_defaults = json.load(args.json)
    parser.set_defaults(**new_defaults)
    (args, _) = parser.parse_known_args()

  add_args_from_schema(parser, args.name)
  if args.json:
    # Override the defaults for the given pipeline
    parser.set_defaults(**new_defaults)

  args = parser.parse_args()
  if args.info:
    parser = argparse.ArgumentParser()
    parser.formatter_class = argparse.ArgumentDefaultsHelpFormatter
    pl = globals()['PL_%s' % args.name]({})
    parser.usage = "\n\n%s (%s)"  % (pl.__doc__, args.name)
    add_args_from_schema(parser, args.name)
    parser.parse_args(['-h'])
  return args

if __name__ == "__main__":
  args = parse_cli_args()
  conf = gen_conf(args.__dict__)
  json.dump(conf, args.output, sort_keys=True, indent=4)
  args.output.write("\n")
