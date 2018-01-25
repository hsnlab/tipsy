# TIPSY: Telco pIPeline benchmarking SYstem
#
# Copyright (C) 2018 by it's authors (See AUTHORS)
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

import logging

# INSTALLATION
# 1. Copy the file to ~ryu/ryu/contrib
# 2. $ pip uninstall ryu
# 3. ~ryu $ pip install .

# https://en.wikipedia.org/wiki/ANSI_escape_code#Colors
colors = {'DEBUG'    : '39;49',
          'INFO'     : '32',
          'WARNING'  : '33',
          'ERROR'    : '31',
          'CRITICAL' : '33;41',
}

class Formatter(logging.Formatter):

    def format(self, record):
        color = colors.get(record.levelname, colors['DEBUG'])
        f = "\033[%sm%%s\033[0m" % color
        record.msg = f % record.msg
        return super(Formatter, self).format(record)
