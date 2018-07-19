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

from plot_base import Plot as Base


def ensure_list(object_or_list):
    if type(object_or_list) == list:
        return object_or_list
    else:
        return [object_or_list]

def str2tex(s):
    return s.replace('_', '$\_$')


class Plot(Base):
    def __init__(self, conf):
        super().__init__(conf)
        self.ylabel = None

    def format_matplotlib(self, series, title):
        import matplotlib as mpl # "Generating graphs w/o a running X server"
        mpl.use('Agg')           # https://stackoverflow.com/a/4935945
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
