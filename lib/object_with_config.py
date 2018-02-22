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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.    See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import logging
import json

class ObjectView(object):
  def __init__(self, **kwargs):
    self.__dict__.update(kwargs)

  def __repr__(self):
    return self.__dict__.__repr__()

  def __getattr__(self, name):
    return self.__dict__[name.replace('_', '-')]

  def get (self, attr, default=None):
    return self.__dict__.get(attr, default)


class ObjectWithConfig(object):
  def __init__(self):
    self.logger = logging.getLogger(__name__)

  def parse_conf(self, var_name, fname):
    self.logger.info("conf_file: %s" % fname)

    try:
      with open(fname, 'r') as f:
        conv_fn = lambda d: ObjectView(**d)
        config = json.load(f, object_hook=conv_fn)
    except IOError as e:
      self.logger.error('Failed to load cfg file (%s): %s' %
                        (fname, e))
      raise(e)
    except ValueError as e:
      self.logger.error('Failed to parse cfg file (%s): %s' %
                        (fname, e))
      raise(e)
    setattr(self, var_name, config)
