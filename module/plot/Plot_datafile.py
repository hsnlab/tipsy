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

import collections
import inspect
import math

from Plot_simple import Plot as Plot_simple
from plot import eval_expr


def ensure_list(object_or_list):
    if type(object_or_list) == list:
        return object_or_list
    else:
        return [object_or_list]

def str2tex(s):
    return s.replace('_', '$\_$')


class Plot(Plot_simple):
    def plot(self, raw_data):
        print(f'nemethf ===================================')
        y_axis = ensure_list(self.conf.y_axis)

        if len(y_axis) == 1:
            self.ylabel = y_axis[0]


        series = collections.defaultdict(list)
        for row in raw_data:
            groups = ensure_list(self.conf.group_by)
            datafile = self.conf.datafile.format(**row)
            print(f'datafile:{datafile}')
            key = []
            for group in groups:
                group_val = row.get(group, 'na')
                key.append('%s' % group_val)
            key = '/'.join(key)
            with open(datafile) as f:
                for line in f:
                    series[key].append(tuple([float(i) for i in line.split()]))
            title = self.conf.title.format(**row)

        for k, points in series.items():
            series[k] = sorted(points)

        series = collections.OrderedDict(sorted(series.items()))

        if series:
            #self.format_matplotlib(series, title)
            self.format_latex(series, title)
        else:
            self.format_latex_empty(self.conf.title)

        return series
