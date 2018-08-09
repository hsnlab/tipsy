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

from ryu.lib.packet import in_proto

class SUT_openflow(object):
  def __init__(self, parent, conf):
    self.conf = conf
    self.parent = parent
    self.logger = self.parent.logger
    self.has_tunnels = False
    self.tables = {'drop': 0}

  def get_tunnel_endpoints(self):
    raise NotImplementedError

  def do_unknown(self, action):
    self.logger.error('Unknown action: %s' % action.action)

  @staticmethod
  def get_proto_name (ip_proto_num):
    name = {in_proto.IPPROTO_TCP: 'tcp',
            in_proto.IPPROTO_UDP: 'udp'}.get(ip_proto_num)
    #TODO: handle None
    return name
