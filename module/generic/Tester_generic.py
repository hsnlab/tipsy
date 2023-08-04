# TIPSY: Telco pIPeline benchmarking SYstem
#
# Copyright (C) 2023 by its authors (See AUTHORS)
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

import json
import os
import subprocess
from pathlib import Path

from tester_base import Tester as Base

class Tester(Base):
    def __init__(self, conf):
        super().__init__(conf)
        tester = conf.tester
        self.script = tester.script
        #TODO: self.runtime = tester.runtime
        self.env = os.environ.copy()
        for var, val in tester.environ.items():
            self.env[var] = str(val)

    def _run(self, out_dir):
        self.out_dir = out_dir
        cmd = self.script
        subprocess.call(cmd, shell=True, cwd=out_dir, env=self.env)

    def collect_results(self):
        try:
            with open(Path(self.out_dir)/'result.json') as f:
                res = json.load(f)
        except FileNotFoundError:
            res = {'error': f'not found: {self.out_dir}/result.json'}
        self.result.update(res)
