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

from tester_base import Tester as Base
from Tester_moongen import Tester as Tester_moongen
from Tester_moongen_rfc2544 import Tester as Tester_moongen_rfc2544

class Tester(Base):
    def __init__(self, conf):
        super().__init__(conf)
        self.rfc2544 = Tester_moongen_rfc2544(conf)
        self.latency = Tester_moongen(conf)

    def _run(self, out_dir):
        self.rfc2544._run(out_dir)
        self.rfc2544.collect_results()
        limit = self.rfc2544.result['rfc2544']['limit']
        self.latency.rate_limit = limit
        self.latency._run(out_dir)

    def collect_results(self):
        self.latency.collect_results()
        self.result.update(self.rfc2544.result)
        self.result.update(self.latency.result)
