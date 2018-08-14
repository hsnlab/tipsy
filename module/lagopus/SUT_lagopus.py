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

import find_mod
Base = find_mod.find_class('SUT', 'openflow')

class SUT(Base):
    def __init__(self, conf):
        super().__init__(conf)
        self.virtualenv = self.conf.sut.lagopus_virtualenv

    def _query_version(self):
        v = self.run_ssh_cmd(['lagopus', '--version'], stdout=subprocess.PIPE)
        first_line = v.stdout.decode('utf8').split("\n")[0]
        self.result['version'] = first_line.split(' ')[-1]
