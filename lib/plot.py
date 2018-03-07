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
import math
from pathlib import Path

try:
    import mongoquery
except ImportError:
    mongoquery = None


def ensure_list(object_or_list):
    if type(object_or_list) == list:
        return object_or_list
    else:
        return [object_or_list]

def str2tex(s):
    return s.replace('_', '$\_$')

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


class Plot(object):
    def __init__(self, conf):
        self.conf = conf

    def plot(self, filtered_data):
        raise NotImplementedError


class Plot_simple(Plot):
    def __init__(self, conf):
        super().__init__(conf)
        self.ylabel = None

    def format_matplotlib(self, series, title):
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

    def latex_extra_plot(self, name, points):
        return ""

    def format_latex(self, series, title):
        def is_empty(curve):
            return all([math.isnan(p[1]) for p in curve[1]])

        # Put empty series to the end, otherwise markers and lables
        # are misaligned in the legend.
        ordered_series = sorted(series.items(), key=is_empty)

        addplot = ""
        for name, points in ordered_series:
            addplot += "\n"
            addplot += r"    \addplot coordinates {" + "\n"
            for p in points:
                addplot += "      (%s, %s)\n" % p
            addplot += "    };\n"
            addplot += r'    \addlegendentry{%s}' % str2tex(name)
            addplot += "\n"
            addplot += self.latex_extra_plot(name, points)
        f = {
            'title': title,
            'xlabel': str2tex(self.conf.x_axis),
            'axis_type': self.get_latex_axis_type(),
            'addplot': addplot,
        }
        if self.ylabel:
            f['ylabel_opt'] = 'ylabel=%s,' % str2tex(self.ylabel)
        else:
            f['ylabel_opt'] = ""
        text = inspect.cleandoc(r"""
          \begin{{figure}}
            \centering
            \begin{{tikzpicture}}
            \begin{{{axis_type}}}[
                xlabel={xlabel}, {ylabel_opt}
                legend pos=outer north east,
                legend cell align=left,
            ]
            {addplot}
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
            x = row[self.conf.x_axis]
            groups = ensure_list(self.conf.group_by)
            for var_name in y_axis:
                y = float(row[var_name])
                if len(y_axis) == 1:
                    key = []
                else:
                    key = [var_name]
                for group in groups:
                    group_val = row.get(group, 'na')
                    key.append('%s' % group_val)
                key = '/'.join(key)
                series[key].append((x, y))
            title = self.conf.title.format(**row)

        for k, points in series.items():
            series[k] = sorted(points)

        series = collections.OrderedDict(sorted(series.items()))
        if series:
            self.format_matplotlib(series, title)
            self.format_latex(series, title)

        return series


class Plot_USL(Plot_simple):
    def latex_extra_plot(self, name, points):
        import lmfit
        import numpy as np

        def usl(params, x, data):
            l = params['lambd']
            s = params['sigma']
            k = params['kappa']

            model = l * x / (1 + s * (x - 1) + k * x * (x - 1))

            return model - data


        x = np.array([x for x, y in points])
        data = [y for x, y in points]
        params = lmfit.Parameters()
        try:
            params.add('lambd', value=points[0][1]/points[0][0])
        except:
            params.add('lambd', value=1)
        params.add('sigma', value=0.1, min=0)
        params.add('kappa', value=0.01)
        # method: leastsq least_squares differential_evolution brute nelder
        method = 'leastsq'
        out = lmfit.minimize(usl, params, method=method, args=(x, data))
        lmfit.report_fit(out)
        print(out.params)
        f = {key: param.value for key, param in out.params.items()}
        if f['kappa'] != 0:
            f['ymax'] = math.floor(math.sqrt((1-f['sigma'])/f['kappa']))
        else:
            f['ymax'] = '$\inf$'
        f['legend'] = ('$\lambda = {lambd:.3f}, \sigma={sigma:.3f},'
                       '\kappa={kappa:.3f}, n_{{max}}={ymax:d}$').format(**f)
        s = inspect.cleandoc(r"""
           \addplot [blue, domain=1:16] {{
            {lambd} * x / (1 +  {sigma}*(x-1) + {kappa} *x*(x-1))
           }};
           \addlegendentry{{{legend}}}
           """.format(**f))

        return s


class Plot_contour(Plot_simple):

    def format_latex(self, data, title):
        addplot = ""
        for x, ys in sorted(data.items()):
            addplot += '     '
            for y, z in sorted(ys.items()):
                addplot += "(%s, %s, %s) " % (x, y, z)
            addplot += "\n\n"
        f = {
            'title': title,
            'xlabel': self.conf.x_axis,
            'ylabel': self.conf.y_axis,
            'addplot': addplot,
            'axis': self.get_latex_axis_type()
        }
        print(addplot, f)
        text = inspect.cleandoc(r"""
          \begin{{figure}}
            \centering
            \begin{{tikzpicture}}
            \begin{{{axis}}}[
                view={{0}}{{90}},
                xlabel={xlabel},
                ylabel={ylabel},
            ]
              \addplot3 [ contour gnuplot] coordinates {{

          {addplot}

              }};
              \end{{{axis}}}
            \end{{tikzpicture}}
            \caption{{{title}}}
          \end{{figure}}
          """
        ).format(**f)
        with open('fig.tex', 'w') as f:
            f.write(text)
            f.write("\n")

    def plot(self, raw_data):
        self.xlabel = self.conf.x_axis
        self.ylabel = self.conf.y_axis

        title = ''
        data = collections.defaultdict(dict)
        for row in raw_data:
            x = float(row[self.conf.x_axis])
            y = float(row[self.conf.y_axis])
            z = float(row[self.conf.z_axis])
            data[x][y] = z
            title = self.conf.title.format(**row)

        if data:
            self.format_latex(data, title)

        return data


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

    plt = globals()['Plot_%s' % conf.type](conf)
    plot_points = plt.plot(data)
    with open('out.json', 'w') as f:
        json.dump(plot_points, f, indent=1)


if __name__ == "__main__":
    run_in_cwd()
