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

def byte_seq (template, seq, offset_first=1):
  try:
    return template % (int(seq / 64516) + offset_first,
                       int(seq % 64516 / 254),
                       (seq % 254) + 1)
  except TypeError:
    return template % (int(seq / 254), (seq % 254) + 1)


class GenConf (object):

  def __init__ (self, args):
    self.args = args
    self.components = ['base']
    self.conf = {}

  def get_arg (self, arg_name, default=None):
    return self.args.__dict__.get(arg_name.replace('-', '_'), default)

  def create_conf (self):
    for c in self.components:
      method = getattr(self, 'add_%s' % c)
      method()
    return self.conf

  def add_base (self):
    self.conf['name'] = self.args.name
    self.conf['core'] = self.args.core
    self.conf['run_time'] = [] # Commands to be executed in every second

  def add_fakedrop (self):
    self.conf['fakedrop'] = self.args.fakedrop

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
    max_port = 65023
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

  def create_l3_table (self, size, nhops, addr_template, offset_first=1):
    table = []
    for i in range(size):
      table.append({
        'ip': byte_seq(addr_template, i, offset_first=offset_first),
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

