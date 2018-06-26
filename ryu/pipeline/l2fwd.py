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

from .base import PL_base

class PL(PL_base):
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
    super(PL, self).__init__(parent, conf)
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

