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

import datetime
import subprocess
import time
from pathlib import Path

class Tester(object):
    def __init__(self, conf):
        self.conf = conf
        self.result = {}

        cwd = str(Path(__file__).parent)
        cmd = ['git', 'describe', '--dirty', '--always', '--tags']
        try:
            v = subprocess.run(cmd, stdout=subprocess.PIPE, cwd=cwd, check=True)
            self.result['tipsy-version'] = v.stdout.decode().strip()
        except Exception as e:
            self.result['tipsy-version'] = 'n/a'
            self.result['tipsy-version-error-msg'] = str(e)

    def run(self, out_dir):
        self.result['timestamp'] = int(time.time())
        self.result['iso-date'] =  datetime.datetime.now().isoformat()
        self.result['test-id'] = out_dir.name
        self.run_setup_script()
        self._run(out_dir)
        self.run_teardown_script()
        self.collect_results()

    def _run(self, out_dir):
        raise NotImplementedError

    def collect_results(self):
        raise NotImplementedError

    def run_script(self, script):
        if Path(script).is_file():
            subprocess.run([str(script)], check=True)

    def run_setup_script(self):
        self.run_script(self.conf.tester.setup_script)

    def run_teardown_script(self):
        self.run_script(self.conf.tester.teardown_script)


