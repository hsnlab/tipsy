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

from pipeline.base import PL_base

class SUT_ovs(PL_base):
  """L2 Port Forwarding

  In the upstream direction the pipeline will receive L2 packets from the
  downlink port of the SUT and forward them to the uplink port. Meanwhile, it
  may optionally rewrite the source MAC address in the L2 frame to the MAC
  address of the uplink port (must be specified by the pipeline config).  The
  downstream direction is the same, but packets are received from the uplink
  port and forwarded to the downlink port after an optional MAC rewrite.
  """
  def __init__(self, parent, conf):
    super(SUT_ovs, self).__init__(parent, conf)
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


