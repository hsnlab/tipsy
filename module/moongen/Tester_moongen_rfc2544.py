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

from Tester_moongen import Tester as Base

class Tester(Base):
    def __init__(self, conf):
        super().__init__(conf)
        self.script = self.lua_dir / 'mg-rfc2544.lua'

    def _run(self, out_dir):
        pcap = out_dir / 'traffic.pcap'
        ofile = out_dir / 'mg.rfc2544.csv'
        precision = 5
        cmd = ['sudo', self.mg_cmd, self.script, self.txdev, self.rxdev, pcap,
               '-r', self.runtime, '-p', precision, '-o', ofile,
               '--lossTolerance', self.loss_tolerance]
        cmd = [ str(o) for o in cmd ]
        print(' '.join(cmd))
        subprocess.call(cmd)

    def collect_results(self):
        data = 'nan'
        with open('mg.rfc2544.csv') as f:
            reader = csv.DictReader(f)
            for row in reader:
                data = row
        self.result.update({'rfc2544': data })
