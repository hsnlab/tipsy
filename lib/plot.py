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

import json
from pathlib import Path

try:
    import mongoquery
except ImportError:
    mongoquery = None

import find_mod


class ObjectView(dict):
    def __init__(self, fname=None, **kwargs):
        tmp = {k.replace('_', '-'): v for k, v in kwargs.items()}
        self.update(**tmp)

    def __getitem__(self, x):
        x = x.replace('_', '-')
        if '.' in x:
            [head, tail] = x.split('.', 1)
            return super().__getitem__(head)[tail]
        return super().__getitem__(x)

    def __getattr__(self, x):
        return self.__getitem__(x)

    def get(self, key, default):
        try:
            return self[key]
        except KeyError:
            return default


def json_load(file):
    with file.open('r') as f:
        data = json.load(f, object_hook=lambda x: ObjectView(**x))
    return data

def match_lt(query, obj):
    return obj < query

def match_gt(query, obj):
    return obj > query

def match_not(query, obj):
    return not match(query, obj)

def match(query, obj):
    if type(query) in [int, float, str]:
        return query == obj
    for var, sub_query in query.items():
        if var.startswith('$'):
            m = globals().get('match_%s' % var[1:])
            if m is None:
                raise NotImplementedError(var)
            if not m(sub_query, obj):
                return False
            else:
                continue
        try:
            val = obj[var]
        except KeyError:
            return False
        if not match(sub_query, val):
            return False
    return True

def filter_data(conf, data):
    if not conf.filter:
        return data
    if mongoquery:
        q = mongoquery.Query(conf.filter)
        return filter(q.match, data)

    print("Can't import monoquery, using a subset of the query language")
    return [obj for obj in data if match(conf.filter, obj)]

def run_in_cwd():
    cwd = Path().cwd()
    conf = json_load(cwd / 'plot.json')
    data = []
    for res in sorted((cwd.parent.parent/'measurements').glob('*.json')):
        print(res)
        data += json_load(res)
    data = filter_data(conf, data)

    plt_class = find_mod.find_class('Plot', conf.type)
    plt_obj = plt_class(conf)
    plot_points = plt_obj.plot(data)
    plt_obj.write_preamble()
    with open('out.json', 'w') as f:
        json.dump(plot_points, f, indent=1)


if __name__ == "__main__":
    run_in_cwd()
