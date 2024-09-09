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
from plot import eval_expr


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
            print(f"{name} {points}")
            x = [float(p[0]) for p in points]
            y = [float(p[1]) for p in points]
            print(x)
            print(y)
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

        error_bar = ""
        if self.conf.get("error_bar", None):
            error_bar = r'+ [error bars/.cd,y dir=both,y explicit]'

        addplot = ""
        symbols = list()
        for name, points in ordered_series:
            addplot += "\n"
            addplot += f"    \\addplot{error_bar} coordinates {{\n"
            for p in points:
                addplot += f"      ({p[0]}, {p[1]})"
                if error_bar and len(p) > 2:
                    addplot += f" +- ({p[2]}, {p[2]})"
                addplot += "\n"
                if not isinstance(p[0], (int, float)) and p[0] not in symbols:
                    symbols.append(p[0])
            addplot += "    };\n"
            addplot += r'    \addlegendentry{%s}' % str2tex(name)
            addplot += "\n"
            addplot += self.latex_extra_plot(name, points)

        
        symbolic_str = "{"
        for x in symbols:
            symbolic_str +=f"{x},"
        symbolic_str = symbolic_str[:-1]+"}"
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
        f['other_opts'] = ''
        if len(symbolic_str) > 1:
            f['symbolic_x_coords'] = "symbolic x coords="+symbolic_str+","
        else:
            f['symbolic_x_coords'] = ""
        sep = "\n      "
        for prop in ['xmin', 'xmax', 'ymin', 'ymax']:
            if self.conf.get(prop, None) is not None:
                f['other_opts'] += f"{sep}{prop}={self.conf.get(prop, 0)}"
                sep = ",\n      "
        text = inspect.cleandoc(r"""
          \begin{{figure}}
            \centering
            \begin{{tikzpicture}}
            \begin{{{axis_type}}}[
                xlabel={xlabel}, {ylabel_opt}, {symbolic_x_coords}
                legend pos=outer north east,
                legend cell align=left, {other_opts},
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

    def format_latex_empty(self, title):
        text = inspect.cleandoc(f"""
          \\begin{{figure}}
             \\centering
               (empty)
             \\caption{{{title}}}
           \\end{{figure}}
           """)
        with open('fig.tex', 'w') as f:
            f.write(text)

    def plot(self, raw_data):
        y_axis = ensure_list(self.conf.y_axis)

        if len(y_axis) == 1:
            self.ylabel = y_axis[0]


        series = collections.defaultdict(list)
        for row in raw_data:
            if self.conf.get('y_pipeline', None):
                row = eval_expr(self.conf.y_pipeline, row)
            x = row[self.conf.x_axis]
            groups = ensure_list(self.conf.group_by)
            for idx, var_name in enumerate(y_axis):
                y = row.get(var_name, None)
                if y is None:
                    y = "nan"

                if type(y) != list:
                    raise

                if len(y_axis) == 1 and len(groups) > 0:
                    key = []
                else:
                    key = [var_name]
                for group in groups:
                    group_val = row.get(group, 'na')
                    key.append('%s' % group_val)
                key = '/'.join(key)

                series[key] = list(zip(x,y))
                print(f"{key}: {series[key]}")
            title = self.conf.title.format(**row)


        series = collections.OrderedDict(series.items())
        print(series)

        if series:
            self.format_matplotlib(series, title)
            # self.format_latex(series, title)
        else:
            self.format_latex_empty(self.conf.title)

        return series
