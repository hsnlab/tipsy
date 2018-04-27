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

import argparse
import json
import os
import re
from distutils.util import strtobool

__all__ = ["add_args"]

def check_type_string (string):
  return string

def check_type_boolean (string):
  return bool(strtobool(string))

def check_type_number (string):
  return float(string)

def check_type_positive_integer (string):
  msg = "'%s' is not a positive integer" % string
  try:
    i = int(string)
  except ValueError:
    raise argparse.ArgumentTypeError(msg)
  if i <= 0:
    raise argparse.ArgumentTypeError(msg)
  return i

def check_type_non_negative_integer (string):
  msg = "'%s' is not non-negative integer" % string
  try:
    i = int(string)
  except ValueError:
    raise argparse.ArgumentTypeError(msg)
  if i < 0:
    raise argparse.ArgumentTypeError(msg)
  return i

def check_type_readable_file (string):
  return argparse.FileType('r')(string)

def check_type_readable_file_or_null (val):
  if val is None:
    return None
  return argparse.FileType('r')(val)

def check_type_writable_file (string):
    return argparse.FileType('w')(string)

def check_type_ip_address (string):
  msg = "'%s' is not an ip address" % string
  l = string.split('.')
  if len(l) != 4:
    raise argparse.ArgumentTypeError(msg)
  for i in l:
    if int(i) > 255 or int(i) < 0:
      raise argparse.ArgumentTypeError(msg)
  return string

def check_type_mac_address (string):
  if re.match(r'^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$', string):
    return string
  msg = "'%s' is not a mac address" % string
  raise argparse.ArgumentTypeError(msg)

def check_type_mac_address_or_null (string):
  if string.lower() in ['null', 'none']:
    return None
  if re.match(r'^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$', string):
    return string
  msg = "'%s' is not a mac address" % string
  raise argparse.ArgumentTypeError(msg)

def add_args(parser, schema_name, schema_dir=None, ignored_properties=[]):
  "Add per pipeline CLI args from the schema definition"

  if schema_dir is None:
      schema_dir = os.path.dirname(os.path.realpath(__file__))
      schema_dir = os.path.join(schema_dir, '..', 'schema')

  fname = '%s.json' % schema_name
  with open(os.path.join(schema_dir, fname)) as f:
    schema = json.load(f)

  for prop, val in sorted(schema['properties'].items()):
    if val.get('$ref'):
      m = re.search(r'#\/(.*)$', val['$ref'])
      type_name = re.sub(r'-', '_', m.group(1))
    else:
      type_name = val['type']
    a = ['--%s' % prop]
    kw = {'help': val['description']}
    if type_name == 'string' and 'enum' in val:
        kw['type'] = str
        kw['choices'] = [str(s) for s in val['enum']]
    else:
        kw['type'] = globals()['check_type_%s' % type_name]
        kw['metavar'] = type_name.upper()
    if 'short_opt' in val:
        a.append(val['short_opt'])
    if 'default' in val:
      kw['default'] = val['default']
    if prop in schema['required']:
        kw['required'] = True
    if type_name == 'boolean':
        kw['nargs'] = '?'
        kw['const'] = True
    if prop not in ignored_properties:
      parser.add_argument(*a, **kw)

