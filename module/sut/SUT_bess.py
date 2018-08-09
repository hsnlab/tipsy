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

import subprocess
from pathlib import Path

from sut_base import SUT as Base

class SUT(Base):
    def _start(self):
        self.result['versions'] = {}
        cmd = [str(Path(self.conf.sut.bess_dir) / 'bin' / 'bessd'), '-t']
        v = self.run_ssh_cmd(cmd, stdout=subprocess.PIPE)
        for line in v.stdout.decode('utf-8').split("\n"):
            if line.startswith(' '):
                break
            [var, val] = line.split(' ')
            self.result['versions'][var] = val
        self.result['version'] = self.result['versions'].get('bessd', 'n/a')

        remote_dir = Path('/tmp')
        self.upload_conf_files(remote_dir)
        cmd = [
            Path(self.conf.sut.tipsy_dir) / 'bess' / 'bess-runner.py',
            '-d', self.conf.sut.bess_dir,
            '-p', remote_dir / 'pipeline.json',
            '-b', remote_dir / 'benchmark.json',
        ]
        self.run_async_ssh_cmd([str(c) for c in cmd])
        self.wait_for_callback()


