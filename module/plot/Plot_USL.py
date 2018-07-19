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
import math

from Plot_simple import Plot as Plot_simple

class Plot(Plot_simple):
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
        params.add('kappa', value=0.01, min=0)
        # method: leastsq least_squares differential_evolution brute nelder
        method = 'leastsq'
        out = lmfit.minimize(usl, params, method=method, args=(x, data))
        lmfit.report_fit(out)
        print(out.params)
        f = {key: param.value for key, param in out.params.items()}
        try:
            f['ymax'] = math.floor(math.sqrt((1-f['sigma'])/f['kappa']))
        except:
            f['ymax'] = '\inf'
        f['legend'] = ('$\lambda = {lambd:.3f}, \sigma={sigma:.3f},'
                       '\kappa={kappa:.3f}, n_{{max}}={ymax}$').format(**f)
        s = inspect.cleandoc(r"""
           \addplot [blue, domain=1:16] {{
            {lambd} * x / (1 +  {sigma}*(x-1) + {kappa} *x*(x-1))
           }};
           \addlegendentry{{{legend}}}
           """.format(**f))

        return s
