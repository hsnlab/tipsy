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
        remote_dir = Path('/tmp')
        self.upload_conf_files(remote_dir)
        cmd = [
            'python3',
            Path(self.conf.sut.tipsy_dir) / 'vpp' / 'vpp-runner.py',
            '-p', remote_dir / 'pipeline.json',
            '-b', remote_dir / 'benchmark.json'
        ]
        self.run_async_ssh_cmd([str(c) for c in cmd])
        self.wait_for_callback()

        cmd = ['sudo', 'vppctl', 'show', 'version']
        v = self.run_ssh_cmd(cmd, stdout=subprocess.PIPE)
        self.result['version'] = v.stdout.decode('utf-8').split("\n")[0]
        dpdk_cmd = ['sudo', 'vppctl', 'show', 'dpdk', 'version']
        d = self.run_ssh_cmd(dpdk_cmd, stdout=subprocess.PIPE)
        fline = d.stdout.decode('utf-8').split("\n")[0]
        dpdk_version = fline.split('DPDK')[-1].strip()
        self.result['versions'] = {}
        self.result['versions']['DPDK'] = dpdk_version
