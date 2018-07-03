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

from itertools import izip, chain, repeat
import random
import traceback

from scapy.all import *

def byte_seq(template, seq):
    return template % (int(seq / 254), (seq % 254) + 1)

# https://stackoverflow.com/a/312644
def grouper(n, iterable, padvalue=None):
    "grouper(3, 'abcdefg', 'x') --> ('a','b','c'), ('d','e','f'), ('g','x','x')"
    return izip(*[chain(iterable, repeat(padvalue, n-1))]*n)


class PicklablePacket(object):
    """A container for scapy packets that can be pickled (in contrast
    to scapy packets themselves). https://stackoverflow.com/a/4312192"""

    __slots__ = ['contents', 'time']

    def __init__(self, pkt):
        self.contents = bytes(pkt)
        self.time = pkt.time

    def __call__(self):
        """Get the original scapy packet."""
        pkt = Ether(self.contents)
        pkt.time = self.time
        return pkt


class GenPkt(object):
    def __init__(self, args, conf, in_que, out_que):
        self.args = args
        self.conf = conf
        self.in_que = in_que
        self.out_que = out_que

    def create_work_items(self, job_size):
        pkt_num = self.get_pkt_num()
        pkt_idx = list(range(pkt_num))
        random.shuffle(pkt_idx)
        items = []
        for job_idx, pkt_idxs in enumerate(grouper(job_size, pkt_idx)):
            pkt_idxs = [idx for idx in pkt_idxs if idx is not None]
            items.append({'job_idx': job_idx, 'pkt_idxs': pkt_idxs})
        return items

    def do_work(self):
        try:
            while True:
                item = self.in_que.get()
                if item is None:
                    break
                pkt_idxs = item['pkt_idxs']
                pkts = [PicklablePacket(self.gen_pkt(idx)) for idx in pkt_idxs]
                item['pkts'] = pkts
                del item['pkt_idxs']
                self.out_que.put(item)
        except Exception as e:
            item = {'exception': e, 'traceback': traceback.format_exc()}
            self.out_que.put(item)
            return True

    def gen_pkt(self, pkt_idx):
        raise NotImplementedError

    def get_pkt_num(self):
        "Return the number of packets to be generated"
        if self.args.pkt_num:
            self.args.auto_pkt_num = False
            return self.args.pkt_num
        self.args.auto_pkt_num = True
        return self.get_auto_pkt_num()

    def get_auto_pkt_num(self):
        raise NotImplementedError

    @staticmethod
    def add_payload(p, pkt_size):
        if len(p) < pkt_size:
            #"\x00" is a single zero byte
            s = "\x00" * (pkt_size - len(p))
            p = p / Raw(s)
        return p

    def get_other_direction(self):
        return {'u': 'd', 'd': 'u'}[self.args.dir[0]]


