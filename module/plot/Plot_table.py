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

import inspect

from Plot_simple import Plot as Plot_base
from Plot_simple import str2tex, ensure_list

class Plot(Plot_base):
    def __init__(self, conf):
        super().__init__(conf)
        self.preamble += inspect.cleandoc(r"""
          \usepackage{pgfplotstable}
          \usepackage{colortbl}
        """) + "\n"

    def sort_by_column(self, table, header, column_name=None):
        if column_name is None:
            return table
        header, table = table[0], table[1:]
        column_idx = header.index(column_name)
        return [header] + sorted(table, key=lambda x: x[column_idx])

    def format_matplotlib(self, series, title):
        pass

    def format_latex(self, series, title):
        f = {'title': title, 'plot_args': '', 'addplot': ''}

        names = [self.conf.x_axis] + [s[0] for s in series.items()]
        table = [names]

        x_vals = []
        for s in series.items():
            x_vals += list(zip(*s[1]))[0]
        x_vals = sorted(set(x_vals))

        sdict = [{k: v for k, v in points} for points in series.values()]
        for x in x_vals:
            vals = [str(x)]
            for s in sdict:
                vals.append(str(s.get(x, 'nan')))
            table.append(vals)

        sort_column = self.conf.get('sort_column', None)
        table = self.sort_by_column(table, sort_column)

        if len(table) < len(table[0]):
            # more columns than rows -> transpose
            table = list(zip(*table))
        if len(x_vals) == 1:
            table = [['variable', 'value']] + table
        header, table = table[0], table[1:]

        column_types = {}
        for colnum, col in enumerate(zip(*table)):
            for val in col:
                if val == 'nan':
                    continue
                try:
                    float(val)
                except ValueError:
                    column_types[colnum] = 'string type, column type=l,'

        for colnum, name in enumerate(header):
            col_type = column_types.get(colnum, "")
            s = ",\n    columns/%d/.style={%s column name={%s}}"
            f['plot_args'] += s % (colnum, col_type, str2tex(name))

        for row in table:
            row = [str2tex(cell) for cell in row]
            f['addplot'] += ' & '.join(row) + "\\\\\n    "

        text = inspect.cleandoc(r"""
          \begin{{table}}
            \tiny
            \centering
            \caption{{{title}}}
            \pgfplotstabletypeset[col sep=&,row sep=\\,header=false {plot_args},
              every even row/.style={{before row={{\rowcolor[gray]{{0.9}}}}}}]{{
              {addplot}}}
          \end{{table}}
          """
        ).format(**f)
        with open('fig.tex', 'w') as f:
            f.write(text)
            f.write("\n")
