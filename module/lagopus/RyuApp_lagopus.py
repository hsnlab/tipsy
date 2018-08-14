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
      kwargs['switch_type'] = ['lagopus', 'openflow']
    self.core_idx = 0 # next core to allocate in the core_list

    super(RyuApp, self).__init__(*args, **kwargs)

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
    self.change_status('start_lagopus')

    num_cores = self.bm_conf.pipeline.core
    if num_cores == 1:
      num_cores += 1
      self.logger.warn('num_cores is increased to %d' % num_cores)

    corelist = [str(c) for c in self.get_cores(num_cores)]
    self.logger.warn('%s', corelist)
    config = os.path.join(fdir, 'lagopus.dsl')
    # If lagopus is restarted without any delay, it cannot grab the
    # hugepages.  We should wait elsewhere, but this is the only
    # switch exhibiting this behavior.
    time.sleep(3)
    cmd = ['lagopus', '-C', config, '-d',
           '--', '-l%s' % ','.join(corelist), '-n2',
           '--', '-p3']
    subprocess.Popen(cmd, cwd=fdir)

    self.ul_port = int(self.bm_conf.sut.uplink_port) + 1
    self.dl_port = int(self.bm_conf.sut.downlink_port) + 1

    self.change_status('initialize_datapath')

  def stop_datapath(self):
    os.system('echo stop|lagosh')

  def get_tun_port(self, tun_end):
    "Get SUT port to tun_end"
    return self.ports['tun-%s' % tun_end]

  def configure_1(self):
    self.change_status('configure_1')
    parser = self.dp.ofproto_parser

    self.insert_fakedrop_rules()
    self.pl.config_switch(parser)

    # Finally, send and wait for a barrier
    msg = parser.OFPBarrierRequest(self.dp)
    msgs = []
    # doesn't work with ryu-lagopus
    #ofctl.send_stats_request(self.dp, msg, self.waiters, msgs, self.logger)

    self.handle_configured()
