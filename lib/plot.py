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
import inspect
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
    def __init__(self, conf):
        super().__init__(conf)
        self.ylabel = None

    def is_row_relevant(self, row):
        if not self.conf.filter:
            return True
        for filter in ensure_list(self.conf.filter):
            if filter.op == "==":
               if row[filter.var] != filter.val:
                   return False
            elif filter.op == "!=":
               if row[filter.var] == filter.val:
                   return False
            elif filter.op == "<":
               if row[filter.var] >= filter.val:
                   return False
            elif filter.op == ">":
               if row[filter.var] <= filter.val:
                   return False
        return True

    def matplotlib_series(self, series, title):
        import matplotlib.pyplot as plt
        plot_fv = getattr(plt, self.conf.axis_type, plt.plot)
        for name, points in series.items():
            x = [p[0] for p in points]
            y = [p[1] for p in points]
            plot_fv(x, y, '-o', label=name)
        plt.title(title)
        plt.xlabel(self.conf.x_axis)
        if self.ylabel:
            plt.ylabel(self.ylabel)
        plt.legend()
        plt.savefig('fig.png')

    def get_latex_axis_type(self):
        if self.conf.axis_type == 'normal':
            return 'axis'
        return '%saxis' % self.conf.axis_type

    def latex_series(self, series, title):
        addplot = ""
        legend = []
        for name, points in series.items():
            legend.append(name)
            addplot += r"  \addplot coordinates {" + "\n"
            for p in points:
                addplot += "      (%s, %s)\n" % p
            addplot += "  };\n"
        f = {
            'title': title,
            'xlabel': self.conf.x_axis,
            'axis_type': self.get_latex_axis_type(),
            'addplot': addplot,
            'legend': ",\n     ".join(legend)
        }
        if self.ylabel:
            f['ylabel_opt'] = 'ylabel=%s,' % self.ylabel
        text = inspect.cleandoc(r"""
          \begin{{figure}}
            \centering
            \begin{{tikzpicture}}
            \begin{{{axis_type}}}[
                xlabel={xlabel},
                {ylabel_opt}
                legend pos=outer north east,
                legend cell align=left,
            ]
            {addplot}
            \legend{{
               {legend}}}
            \end{{{axis_type}}}
            \end{{tikzpicture}}
            \caption{{{title}}}
          \end{{figure}}
          """
        ).format(**f)
        with open('fig.tex', 'w') as f:
            f.write(text)
            f.write("\n")

    def plot(self, raw_data):
        y_axis = ensure_list(self.conf.y_axis)
        if len(y_axis) == 1:
            self.ylabel = y_axis[0]

        series = collections.defaultdict(list)
        for row in raw_data:
            if not self.is_row_relevant(row):
                continue
            x = row[self.conf.x_axis]
            groups = ensure_list(self.conf.group_by)
            for var_name in y_axis:
                y = float(row[var_name])
                if len(y_axis) == 1:
                    key = []
                else:
                    key = [var_name]
                for group in groups:
                    group_val = row[group]
                    key.append('%s' % group_val)
                key = '/'.join(key)
                series[key].append((x, y))
            title = self.conf.title.format(**row.__dict__)

        series = collections.OrderedDict(sorted(series.items()))
        if series:
            self.matplotlib_series(series, title)
            self.latex_series(series, title)

        with open('out.json', 'w') as f:
            json.dump(series, f, indent=1)


def json_load(file):
    with file.open('r') as f:
        data = json.load(f, object_hook=lambda x: ObjectView(**x))
    return data

def run_in_cwd():
    cwd = Path().cwd()
    conf = json_load(cwd / 'plot.json')
    data = []
    for res in (cwd.parent.parent/'measurements').glob('*.json'):
        print(res)
        data += json_load(res)

    plt = globals()['Plot_%s' % conf.type](conf)
    plt.plot(data)


if __name__ == "__main__":
    run_in_cwd()
