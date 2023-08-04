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

from pathlib import Path

import find_mod
Base = find_mod.find_class('SUT', 'base')

class SUT(Base):
    def _start(self):
        pass

    def stop(self):
        self.run_teardown_script()
