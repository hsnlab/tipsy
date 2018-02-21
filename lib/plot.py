#!/usr/bin/env python3

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
import json

from pathlib import Path

def ensure_list(object_or_list):
    if type(object_or_list) == list:
        return object_or_list
    else:
        return [object_or_list]


class ObjectView(object):
    def __init__(self, fname=None, **kwargs):
        tmp = {k.replace('-', '_'): v for k, v in kwargs.items()}
        self.__dict__.update(**tmp)

    def __getitem__(self, x):
        x = x.replace('-', '_')
        if '.' in x:
            [head, tail] = x.split('.', 1)
            return self.__dict__[head][tail]
        return self.__dict__[x]

    def __repr__(self):
        return self.__dict__.__repr__()


class Plot(object):
    def __init__(self, conf):
        self.conf = conf

    def plot(self, data):
        raise NotImplementedError

class Plot_simple(Plot):
    def plot(self, raw_data):
        y_axis = ensure_list(self.conf.y_axis)
        series = collections.defaultdict(list)
        for row in raw_data:
            x = row[self.conf.x_axis]
            groups = ensure_list(self.conf.group_by)
            for var_name in y_axis:
                y = float(row[var_name])
                key = var_name
                for group in groups:
                    group_val = row[group]
                    key += '/%s' % group_val
                series[key].append((x, y))
            title = self.conf.title.format(**row.__dict__)

        import matplotlib.pyplot as plt
        for name, points in series.items():
            x = [p[0] for p in points]
            y = [p[1] for p in points]
            plt.plot(x, y, '-o', label=name)
        plt.title(title)
        plt.xlabel(self.conf.x_axis)
        plt.legend()
        plt.savefig('fig.png')

        with open('out.json', 'w') as f:
            json.dump(series, f, indent=1)


def json_load(file):
    with file.open('r') as f:
        data = json.load(f, object_hook=lambda x: ObjectView(**x))
    return data

def run_in_cwd():
    cwd = Path().cwd()
    conf = json_load(cwd / 'plot.json')
    data = json_load(cwd.parent.parent / 'measurements' / 'result.json')

    plt = globals()['Plot_%s' % conf.type](conf)
    plt.plot(data)


if __name__ == "__main__":
    run_in_cwd()
