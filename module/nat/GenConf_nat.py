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

from gen_conf_base import GenConf as Base

class GenConf (Base):
  "SNAT pipeline"
  def __init__ (self, args):
    super().__init__(args)
    self.components += ['nat']

  def add_nat (self):
    for arg in ['downlink-dst-mac', 'uplink-dst-mac',
                'range-ipv4-min', 'range-ipv4-max',
                'range-port-min', 'range-port-max']:
      self.conf[arg] = self.get_arg(arg)
