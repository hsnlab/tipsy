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

import os
import re
import subprocess
import sys

from ryu.lib import hub


fdir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(fdir, '..', '..', 'lib'))
import find_mod

RyuAppOpenflow = find_mod.find_class('RyuApp', 'openflow')

class RyuApp(RyuAppOpenflow):
  def __init__(self, *args, **kwargs):
    if 'switch_type' not in kwargs:
      kwargs['switch_type'] = ['erfs', 'openflow']
    super(RyuApp, self).__init__(*args, **kwargs)
    self.core_idx = 0 # next core to allocate in the core_list
    with find.add_path(os.path.dirname(__file__)):
      import sw_conf_erfs as sw_conf
      self.sw_conf = sw_conf

  def add_port(self, br_name, port_name, iface, core=1):
    self.sw_conf.add_port(br_name, port_name, iface, core)

  def get_cores(self, num_cores):
    coremask = self.bm_conf.sut.coremask
    cpum = int(coremask, 16)
    core_list = [i for i in range(32) if (cpum >> i) & 1 == 1]
    ## lcore 0 is reserved for the controller
    #core_list = [i for i in core_list if i != 0]
    cores = []
    for i in range(num_cores):
      cores.append(core_list[self.core_idx])
      self.core_idx = (self.core_idx + 1) % len(core_list)
    return cores

  def initialize_datapath(self):
    self.change_status('start_erfs')

    coremask = self.bm_conf.sut.coremask
    os.system('pkill dof')
    #cmd = ['./dof', '-c', coremask, '--socket-mem=1024,1024', '--', '-d', '10']
    cmd = ['./dof', '-c', coremask, '--', '-d', '10']
    cwd = self.bm_conf.sut.erfs_dir
    subprocess.Popen(cmd, cwd=cwd)
    time.sleep(15)

    self.change_status('initialize_datapath')
    self.sw_conf.init_sw()

    br_num = 1 # 'br-main'
    self.sw_conf.add_bridge(br_num)

    core = self.get_cores(self.bm_conf.pipeline.core)
    self.add_port(br_num, 1, self.ul_port_name, core=core)
    self.add_port(br_num, 2, self.dl_port_name, core=core)
    self.ul_port = 1
    self.dl_port = 2

    hub.spawn_after(1, self.sw_conf.set_controller, br_num, '127.0.0.1')

  def stop_datapath(self):
    self.sw_conf.del_bridge(1)

  def get_tun_port(self, tun_end):
    "Get SUT port to tun_end"
    return self.ports['tun-%s' % tun_end]
