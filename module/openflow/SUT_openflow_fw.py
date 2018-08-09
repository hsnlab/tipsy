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

import os
import sys
fdir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(fdir, '..', '..', 'lib'))
import find_mod
Base = find_mod.find_class('SUT_openflow', 'base')

class SUT_openflow(Base):
  """Firewall (ACL) pipeline
  """

  def __init__(self, parent, conf):
    super(SUT_openflow, self).__init__(parent, conf)
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

    for d in ['u', 'd']:
      longname = {'u': 'upstream', 'd': 'downstream'}[d]
      for entry in self.conf.get('%sl_fw_rules' % d):
        self.mod_table('add', longname, entry)

  def mod_table(self, cmd, table, entry):
    mod_flow = self.parent.mod_flow
    out_port = {'upstream': self.parent.ul_port,
                'downstream': self.parent.dl_port}[table]
    match = {
      'eth_type': ETH_TYPE_IP,
      'ipv4_src': entry.src_ip,
      'ipv4_dst': entry.dst_ip,
    }
    pname = self.get_proto_name(entry.ipproto)

    if entry.ipproto > 0:
      match['ip_proto'] = entry.ipproto
      if entry.src_port > 0:
        match['%s_src' % pname] = entry.src_port
      if entry.dst_port > 0:
        match['%s_dst' % pname] = entry.dst_port

    mod_flow(table, match=match, output=out_port, cmd=cmd)

  def do_mod_table(self, args):
    self.mod_table(args.cmd, args.table, args.entry)
