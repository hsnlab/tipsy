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
import binascii
import itertools
import json
import re
import requests
import signal
import struct
import socket
import subprocess
import sys
import time
from pathlib import Path


class BessUpdaterGwlb(BessUpdater):
    def _run(self):
        try:
            i = self.bess.get_module_info('tbl_two_w0_s1')
            self.itype = 'goto'
        except Exception as e:
            self.itype = 'universal'
        while self._running:
            for task in self.conf.run_time:
                if not self._running:
                    return
                getattr(self, 'do_%s' % task.action)(task.args)
            time.sleep(self.runtime_interval)

    def mod_port_universal(self, args, wid):
        name = 'tbl_one_%d' % wid
        ogate = 1
        s = self.conf.service[args.s_idx]
        for b in s.backend:
            src_ip_mask = 0xffffffff ^ (1 << 32 - b.prefix_len) - 1
            src_ip_mask = struct.pack("!I", src_ip_mask)
            values = [
                {'value_bin': struct.pack("!H", 0x0800)}, # ethertype == IP
                {'value_bin': struct.pack("!B", 17)}, # UDP
                {'value_bin': aton(b.ip_src)},
                {'value_bin': aton(s.ip_dst)},
                {'value_bin': struct.pack("!H", int(s.udp_dst))},
            ]
            masks = [
                {'value_int': 0xffff},
                {'value_int': 0xff},
                {'value_bin': src_ip_mask},
                {'value_int': 0xffFFffFF},
                {'value_int': 0xffff},
            ]
            argsD = {'values': values, 'masks': masks}
            argsA = {'values': values, 'masks': masks, 'gate': ogate}
            self.bess.pause_worker(wid)
            self.bess.run_module_command(
                name, 'delete', 'WildcardMatchCommandDeleteArg', argsD)
            self.bess.run_module_command(
                name, 'add', 'WildcardMatchCommandAddArg', argsA)

    def mod_port_goto(self, args, wid):
        name = 'tbl_one_%d' % wid
        ogate = int(args.s_idx)
        s = self.conf.service[args.s_idx]
        fields = [
            {'value_bin': struct.pack("!H", 0x0800)},
            {'value_bin': struct.pack("!B", 17)}, # UDP
            {'value_bin': aton(s.ip_dst)},
            {'value_bin': struct.pack("!H", int(s.udp_dst))},
        ]
        argsD = {'fields': fields}
        argsA = {'fields': fields, 'gate': ogate}
        self.bess.pause_worker(wid)
        self.bess.run_module_command(
            name, 'delete', 'ExactMatchCommandDeleteArg', argsD)
        self.bess.run_module_command(
            name, 'add', 'ExactMatchCommandAddArg', argsA)

    def do_mod_port(self, args):
        for wid in range(self.conf.core):
            try:
                attr = getattr(self, 'mod_port_%s' % self.itype)
                attr(args, wid)
            except BESS.Error:
                raise
            finally:
                self.bess.resume_worker(wid)
