#!/usr/bin/env python3

# TIPSY: Telco pIPeline benchmarking SYstem
#
# Copyright (C) 2018 by its authors (See AUTHORS)
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
import subprocess
from pathlib import Path

try:
  import args_from_schema
  import find_mod
  from gen_conf_base import GenConf, byte_seq
except ImportError:
  from . import args_from_schema
  from . import find_mod
  from .gen_conf_base import GenConf, byte_seq

__all__ = ["gen_conf"]

def gen_conf (args):
  parser = argparse.ArgumentParser()
  add_args_from_schema(parser, args.get('name', 'mgw'))
  set_defaults(parser, **args)
  args = parser.parse_args([])

  pl_class = find_mod.find_class('GenConf', args.name)
  conf = pl_class(args).create_conf()
  return conf

def set_defaults(parser, **defaults):
  defaults = {k.replace('-', '_'): v for k, v in defaults.items()}
  parser.set_defaults(**defaults)


class GenConf_fw (GenConf):
  "Firewall (acl) pipeline"
  def __init__ (self, args):
    super().__init__(args)
    self.components += ['fakedrop', 'acl']

  def add_acl (self):
    rule_num = self.args.rule_num
    cmd = self.args.classbench_cmd
    v_dir = Path(cmd).parent / 'vendor'
    if self.args.output.name == '/dev/stdout': # the default
      outfile = Path('/tmp/fw_rules')
    else:
      outfile = Path(self.args.output.name).parent / 'fw_rules'

    db_genrator = v_dir / 'db_generator' / 'db_generator'
    seed_file = v_dir / 'parameter_files' / ('%s_seed' % self.args.seed_file)
    cmd = [cmd, 'generate', 'v4', seed_file, '--count=%d' % rule_num,
           '--db-generator=%s' % db_genrator]
    cmd = [str(s) for s in cmd]
    rules = []
    with outfile.open('w') as f:
      subprocess.check_call(cmd, stdout=f)
    with outfile.open() as f:
      for line in f.readlines():
        #line = line.decode('utf-8')
        if not line.startswith("@"):
          continue
        line = line[1:]
        fields = line.split("\t")
        src_ports = fields[2].split(" ")
        dst_ports = fields[3].split(" ")
        rules.append({
          "src_ip": fields[0],
          "dst_ip": fields[1],
          "src_port": int(src_ports[0]),
          "dst_port": int(dst_ports[0]),
          "ipproto": int(fields[4].split("/")[0], 16),
          "drop": False
        })
    self.conf.update(
      {'ul_fw_rules': rules,
       'dl_fw_rules': rules,
      })


class GenConf_l2fwd (GenConf):
  "L2 Packet Forwarding pipeline"

  def __init__ (self, args):
    super().__init__(args)
    self.components += ['fakedrop']
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


class GenConf_l3fwd (GenConf):
  "L3 Packet Forwarding pipeline"

  def __init__ (self, args):
    super().__init__(args)
    self.components += ['fakedrop']
    self.components += ['l3', 'sut_mac_addresses']

  def add_l3 (self):
    for i, d in enumerate(['upstream', 'downstream']):
      self.conf['%s_l3_table' % d] = self.create_l3_table(
        size=self.get_arg('%s_l3_table_size' % d),
        nhops=self.get_arg('%s_group_table_size' % d),
        addr_template='%d.%d.%d.2',
        offset_first=50+i*100
      )

    for d in ['upstream', 'downstream']:
      dprefix = {'upstream': 'aa:bb:bb:aa', 'downstream': 'aa:aa:ab:ba'}[d]
      sprefix = {'upstream': 'ee:dd:dd:aa', 'downstream': 'ee:ee:ed:da'}[d]
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
        addr_template='%d.%d.%d.2',
        offset_first=1+i*20
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
      dprefix = {'upstream': 'aa:bb:bb:ff', 'downstream': 'aa:ab:ba:ff'}[d]
      sprefix = {'upstream': 'ee:dd:dd:ff', 'downstream': 'ee:ed:da:ff'}[d]
      extra_nhops = self.create_l2_table(
        size={'upstream': u_nhops, 'downstream': (fluct - u_nhops)}[d],
        dmac_template='%s:%%02x:%%02x' % dprefix,
        smac_template='%s:%%02x:%%02x' % sprefix
      )
      for entry in extra_nhops:
        add_rules = add_rules + [make_rule('add', entry, d)]
        del_rules = [make_rule('del', entry, d)] + del_rules
    self.conf['run_time'] = add_rules + self.conf['run_time'] + del_rules


class GenConf_mgw (GenConf):
  "Mobile Gateway"

  def __init__ (self, args):
    super().__init__(args)
    self.components += ['fakedrop']
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


class GenConf_vmgw (GenConf_mgw):
  "Virtual Mobile Gateway"

  def __init__ (self, args):
    super().__init__(args)
    self.components += ['fakedrop']
    self.components += ['dcgw', 'fw', 'apps']

  def add_apps (self):
    if self.args.napps != 1:
      raise NotImplementedError('Currently, one app is supported.')
    self.conf['napps'] = self.args.napps


class GenConf_bng (GenConf):
  "Broadband Network Gateway"

  def __init__ (self, args):
    super().__init__(args)
    self.components += ['fakedrop']
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

  def add_fluct_user (self):
    self.args.bst = self.args.cpe
    super().add_fluct_user()

def list_pipelines ():
  l = [n[3:] for n in globals() if n.startswith('GenConf_')]
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
    set_defaults(parser, **new_defaults)
    (args, _) = parser.parse_known_args()

  add_args_from_schema(parser, args.name)
  if args.json:
    # Override the defaults for the given pipeline
    set_defaults(parser, **new_defaults)

  args = parser.parse_args()
  if args.info:
    parser = argparse.ArgumentParser()
    parser.formatter_class = argparse.ArgumentDefaultsHelpFormatter
    pl = find_mod.find_class('GenConf', args.name)({})
    parser.usage = "\n\n%s (%s)"  % (pl.__doc__, args.name)
    add_args_from_schema(parser, args.name)
    parser.parse_args(['-h'])
  return args

if __name__ == "__main__":
  args = parse_cli_args()
  conf = gen_conf(args.__dict__)
  json.dump(conf, args.output, sort_keys=True, indent=4)
  args.output.write("\n")
