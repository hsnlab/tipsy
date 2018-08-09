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

#
# ~tipsy/module/unique_id/modName_variant.extension
#

import glob as Glob
import importlib
import inspect
import os
import re
import sys

class add_path():
  def __init__(self, path):
    self.path = path

  def __enter__(self):
    sys.path.insert(0, self.path)

  def __exit__(self, exc_type, exc_value, traceback):
    sys.path.remove(self.path)

def find_file(rel_filename):
  cdir = os.path.dirname(__file__)
  mdir = os.path.join(cdir, '..', 'module')
  for module in os.listdir(mdir):
    base_dir = os.path.abspath(os.path.join(mdir, module))
    if not os.path.isdir(base_dir):
      continue
    path = os.path.join(base_dir, rel_filename)
    path = os.path.abspath(path)
    if os.path.exists(path):
      return path
  return None

def glob(pattern):
  matches = []
  cdir = os.path.dirname(__file__)
  mdir = os.path.join(cdir, '..', 'module')
  for module in os.listdir(mdir):
    base_dir = os.path.abspath(os.path.join(mdir, module))
    if not os.path.isdir(base_dir):
      continue
    matches += Glob.glob(os.path.join(base_dir, pattern))
  return matches

pipelines = []
def list_pipelines(update=False):
  global pipelines
  if pipelines and not update:
    return pipelines

  pl = []
  fdir = os.path.dirname(__file__)
  fn = os.path.join(fdir, '..', 'schema', 'pipeline.json')
  try:
    with open(fn, 'r') as f:
      for line in f:
        m = re.search('pipeline-(.*)\.json#', line)
        if m:
          pl.append(m.group(1))
  except FileNotFoundError:
    pass

  pipelines = sorted(pl)
  return pipelines

def find_class(class_name, variant):
  rel_filename = '%s_%s.py' % (class_name, variant)
  path = find_file(rel_filename)
  if path:
    with add_path(os.path.dirname(path)):
      cl = importlib.import_module('%s_%s' % (class_name, variant), class_name)
      #print('Found rel_filename(%s) as path(%s)' % (rel_filename, path))
      if not inspect.isclass(cl):
        # cl should be the class, but it's the package (?)
        cl = getattr(cl, class_name)
      return cl
  cl = inspect.stack()[1][0].f_globals['%s_%s' % (class_name, variant)]
  return cl

def new(class_name, variant, *args, **kw):
  klass = find_class(class_name, variant)
  return klass(*args, **kw)
