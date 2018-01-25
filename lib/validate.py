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
# This file incorporates work covered by the following copyright and
# permission notice:
#
# Copyright (c) 2013 Julian Berman
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import argparse
import json
import jsonschema  # apt install python3-jsonschema
from copy import deepcopy
from os import path

schema_dir = path.join(path.abspath(path.dirname(__file__)),
                       '..', 'schema')

# Background info:
# https://spacetelescope.github.io/understanding-json-schema/index.html

# This is a slight modifications of jsonschema._validators.oneOf_draft4()
#
# It resets the defaults after backtracking
def oneOf_with_default (validator, oneOf, instance, schema):
  subschemas = enumerate(oneOf)
  all_errors = []
  for index, subschema in subschemas:
    instance2 = deepcopy(instance)
    errs = list(validator.descend(instance2, subschema, schema_path=index))
    if not errs:
      first_valid = subschema
      break
    all_errors.extend(errs)
  else:
    yield jsonschema.ValidationError(
      "%r is not valid under any of the given schemas" % (instance,),
      context=all_errors,
    )

  more_valid = [
    s for i, s in subschemas if validator.is_valid(deepcopy(instance), s)]
  if more_valid:
    more_valid.append(first_valid)
    reprs = ", ".join(repr(schema) for schema in more_valid)
    yield jsonschema.ValidationError(
      "%r is valid under each of %s" % (instance, reprs)
    )
  instance.clear()
  instance.update(instance2)

# http://python-jsonschema.readthedocs.io/en/latest/faq/#why-doesn-t-my-schema-s-default-property-set-the-default-on-my-instance
def extend_with_default (validator_class):
  validate_properties = validator_class.VALIDATORS["properties"]

  def set_defaults(validator, properties, instance, schema):
    for property, subschema in properties.items():
      if "default" in subschema:
        instance.setdefault(property, subschema["default"])

      for error in validate_properties(
          validator, properties, instance, schema,
      ):
        yield error

  #validator_class.VALIDATORS['oneOf'] = oneOf_with_default

  return jsonschema.validators.extend(
    validator_class, {"properties" : set_defaults,
                      "oneOf": oneOf_with_default},
  )


def validate_data (data, schema=None, schema_name=None):
  if schema_name:
    with open(path.join(schema_dir, schema_name + '.json')) as f:
      schema = json.load(f)

  validator = extend_with_default(jsonschema.Draft4Validator)
  #validator = jsonschema.Draft4Validator
  resolver = jsonschema.RefResolver('file://' + schema_dir + '/', schema)
  validator(schema, resolver=resolver).validate(data)

if __name__ == "__main__":
  pipeline_json = path.join(schema_dir, 'pipeline.json')
  parser = argparse.ArgumentParser(
    description='Validate JSON object')
  parser.formatter_class = argparse.ArgumentDefaultsHelpFormatter
  parser.add_argument('--schema', '-s', help="schema defintion file",
                      default=pipeline_json, type=argparse.FileType('r'))
  parser.add_argument('json_file', nargs="?", help="file to validate",
                      default='/dev/stdin', type=argparse.FileType('r'))
  try:
    import argcomplete
    argcomplete.autocomplete(parser)
  except ImportError:
    pass
  args = parser.parse_args()
  schema = json.load(args.schema)
  data = json.load(args.json_file)

  validate_data(data, schema)
  print(json.dumps(data, sort_keys=True, indent=4))
