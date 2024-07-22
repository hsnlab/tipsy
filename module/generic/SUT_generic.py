# TIPSY: Telco pIPeline benchmarking SYstem
#
# Copyright (C) 2024 by its authors (See AUTHORS)
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

import find_mod
Base = find_mod.find_class('SUT', 'base')

class SUT(Base):
    def __init__(self, conf):
        super().__init__(conf)
        sut = conf.sut
        self.script = sut.script
        self.env = os.environ.copy()
        for var, val in sut.environ.items():
            self.env[var] = str(val)

    def _start(self):
        cmd = self.script
        subprocess.run(cmd, shell=True, check=True, env=self.env)

    def stop(self):
        self.run_teardown_script()

    def run_script(self, script):
        if Path(script).is_file():
            subprocess.run(str(script), shell=True, check=True, env=self.env)
