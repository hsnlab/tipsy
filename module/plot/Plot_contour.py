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

from Plot_simple import Plot as Plot_simple

class Plot(Plot_simple):

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


