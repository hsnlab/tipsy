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

import json
import subprocess
from pathlib import Path, PosixPath

import find_mod

__all__ = ["run"]

class Config(dict):
    def __init__(self, *files, **kwargs):
        self.update(kwargs)
        for f in files:
            self.load(f)

    def __getattr__(self, name):
        return self[name.replace('_', '-')]

    def __setattr__(self, name, value):
        self[name.replace('_', '-')] = value

    def __delattr__(self, name):
        del self[name.replace('_', '-')]

    def load(self, file):
        """Update dict with json encode data from `file`.
        file is a filename or Path."""

        oh = lambda x: Config(**x)
        try:
            if type(file) == PosixPath:
                with file.open('r') as f:
                    data = json.load(f, object_hook=oh)
            else:
                with open(file, 'r') as f:
                    data = json.load(f, object_hook=oh)
        except Exception as e:
            print(e)
            exit(-1)
        self.update(**data)


def run(defaults=None):
    cwd = Path().cwd()
    conf = Config(cwd / 'benchmark.json')
    sut = find_mod.new('SUT', conf.sut.type, conf)
    sut.start()

    tester_type = conf.tester.type.replace('-','_')
    tester = find_mod.new('Tester', tester_type, conf)
    tester.run(cwd)

    sut.stop()

    result = conf
    result['out'] = {'sut': sut.result}
    result['out'].update(tester.result)
    with open('results.json', 'w') as f:
        json.dump(result, f, sort_keys=True, indent=4)


if __name__ == "__main__":
    run()
