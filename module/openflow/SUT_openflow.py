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

from pathlib import Path, PosixPath

import find_mod
Base = find_mod.find_class('SUT', 'base')

class SUT(Base):
    def __init__(self, conf):
        super().__init__(conf)
        self.remote_dir = Path(self.conf.sut.tipsy_dir) / 'module' / 'openflow'

    def _start(self):
        self.upload_conf_files('/tmp')

        cmd = self.remote_dir / 'start-ryu'
        self.run_async_ssh_cmd(['sudo', str(cmd)])
        self.wait_for_callback()
