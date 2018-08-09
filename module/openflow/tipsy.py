# TIPSY: Telco pIPeline benchmarking SYstem
#
# Copyright (C) 2017-2018 by its authors (See AUTHORS)
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
"""
TIPSY: Telco pIPeline benchmarking SYstem

Run as:
    $ cd path/to/tipsy.py
    $ ryu-manager --config-dir .
"""
from __future__ import print_function

import json
import os
import sys

from ryu import cfg

fdir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(fdir, '..', '..', 'lib'))
import find_mod

pipeline_conf = '/tmp/pipeline.json'
benchmark_conf = '/tmp/benchmark.json'
cfg.CONF.register_opts([
  cfg.StrOpt('pipeline_conf', default=pipeline_conf,
             help='json formatted configuration file of the pipeline'),
  cfg.StrOpt('benchmark_conf', default=benchmark_conf,
             help='configuration of the whole benchmark (in json)'),
  cfg.StrOpt('webhook_configured', default='http://localhost:8888/configured',
             help='URL to request when the sw is configured'),
  cfg.StrOpt('webhook_failed', default='http://localhost:8888/failed',
             help='URL to request when the configuration is unsuccessful'),
], group='tipsy')
CONF = cfg.CONF['tipsy']


def eprint(*args, **kw):
  print(*args, file=sys.stderr, **kw)

try:
  with open(CONF['benchmark_conf'], 'r') as f:
    bm = json.load(f)
except IOError as e:
  eprint('Failed to load cfg file (%s): %s' % (fname, e))
  raise e
except ValueError as e:
  eprint('Failed to parse cfg file (%s): %s' % (fname, e))
  raise e

App = find_mod.find_class('RyuApp', bm["sut"]["type"])
App.__module__ = 'tipsy'
App.__name__ = 'Tipsy'
