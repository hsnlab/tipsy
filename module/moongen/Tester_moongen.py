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

import csv
import subprocess
from pathlib import Path

from tester_base import Tester as Base

class Tester(Base):
    def __init__(self, conf):
        super().__init__(conf)
        tester = conf.tester
        if conf.traffic.dir == 'uplink':
            self.txdev = tester.uplink_port
            self.rxdev = tester.downlink_port
        elif conf.traffic.dir == 'downlink':
            self.txdev = tester.downlink_port
            self.rxdev = tester.uplink_port
        else:
            raise Exception("unavailable traffic.dir: %s" % conf.traffic.dir)
        if tester.core != 1:
            self.txdev = "%s:%s" % (self.txdev, tester.core)
        self.mg_cmd = tester.moongen_cmd
        self.lua_dir = Path(__file__).parent
        self.script = self.lua_dir / 'mg-pcap.lua'
        self.runtime = tester.test_time
        self.rate_limit = tester.rate_limit
        self.loss_tolerance = tester.loss_tolerance

    def _run(self, out_dir):
        pcap = out_dir / 'traffic.pcap'
        pfix = out_dir / 'mg'
        hfile = out_dir / 'mg.histogram.csv'
        cmd = ['sudo', self.mg_cmd, self.script, self.txdev, self.rxdev, pcap,
               '-l', '-t', '-r', self.runtime, '-o', pfix, '--hfile', hfile]
        if self.rate_limit:
            cmd += ['--rate-limit', self.rate_limit]
        cmd = [ str(o) for o in cmd ]
        print(' '.join(cmd))
        subprocess.call(cmd)

    def collect_results(self):
        with open('mg.latency.csv') as f:
            reader = csv.DictReader(f)
            for row in reader:
                latency = row
        latency['unit'] = 'ns'

        throughput = {}
        with open('mg.throughput.csv') as f:
            # The last rows describe for the overall performance
            reader = csv.DictReader(f)
            for row  in reader:
                d = row.pop('Direction')
                throughput[d] = row
        self.result.update({
            'latency': latency,
            'throughput': throughput
        })
