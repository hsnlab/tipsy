#!/usr/bin/env python

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

# scapy in python3 does not support VXLAN headers,
# so we stick with python2

import argparse
import json
import math
import multiprocessing
import random
import scapy
import sys
import traceback
from itertools import izip, chain, repeat
try:
    from pathlib import PosixPath
except ImportError:
    # python2
    PosixPath = str
from scapy.all import *

try:
    import args_from_schema
except ImportError:
    from . import args_from_schema

__all__ = ["gen_pcap"]


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


class ObjectView(object):
    def __init__(self, d=None, **kwargs):
        if d is None:
            d = kwargs
        d = {k.replace('-', '_'): v for k, v in d.items()}
        self.__dict__.update(**d)

    def __repr__(self):
        return self.__dict__.__repr__()

    def as_dict(self):
        return self.__dict__


def byte_seq(template, seq):
    return template % (int(seq / 254), (seq % 254) + 1)

# https://stackoverflow.com/a/312644
def grouper(n, iterable, padvalue=None):
    "grouper(3, 'abcdefg', 'x') --> ('a','b','c'), ('d','e','f'), ('g','x','x')"
    return izip(*[chain(iterable, repeat(padvalue, n-1))]*n)


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


class GenPkt_portfwd(GenPkt):

    def get_auto_pkt_num(self):
        return 1024

    def gen_pkt(self, pkt_idx):
        smac = byte_seq('aa:bb:bb:aa:%02x:%02x', random.randrange(1, 65023))
        dmac = byte_seq('aa:cc:dd:cc:%02x:%02x', random.randrange(1, 65023))
        dip = byte_seq('3.3.%d.%d', random.randrange(1, 255))
        p = Ether(dst=dmac, src=smac) / IP(dst=dip)
        p = self.add_payload(p, self.args.pkt_size)
        return p


class GenPkt_l2fwd(GenPkt):
    def __init__(self, *args, **kw):
        super(GenPkt_l2fwd, self).__init__(*args, **kw)
        if self.args.dir.startswith('u'):
            self.table = self.conf.upstream_table
        else:
            self.table = self.conf.downstream_table

    def get_auto_pkt_num(self):
        dir = self.args.dir
        if 'd' in dir:  # downstream
            return len(self.conf.downstream_table)
        elif 'u' in dir:  # upstream
            return len(self.conf.upstream_table)
        elif 'b' in dir:  # bidir
            return max(len(self.conf.upstream_table),
                       len(self.conf.downstream_table))
        else:
            raise ValueError

    def gen_pkt(self, pkt_idx):
        dmac = self.table[pkt_idx % len(self.table)].mac
        smac = byte_seq('aa:bb:bb:aa:%02x:%02x', random.randrange(1, 65023))
        dip = byte_seq('3.3.%d.%d', random.randrange(1, 255))
        p = Ether(dst=dmac, src=smac) / IP(dst=dip)
        p = self.add_payload(p, self.args.pkt_size)
        return p


class GenPkt_l3fwd(GenPkt):
    def __init__(self, *args, **kw):
        super(GenPkt_l3fwd, self).__init__(*args, **kw)
        if self.args.dir.startswith('u'):
            self.l3_table = self.conf.upstream_l3_table
        else:
            self.l3_table = self.conf.downstream_l3_table
        self.sut_mac = getattr(self.conf.sut,
                               '%sl_port_mac' % self.get_other_direction())

    def get_auto_pkt_num(self):
        dir = self.args.dir
        if 'd' in dir:  # downstream
            return len(self.conf.downstream_l3_table)
        elif 'u' in dir:  # upstream
            return len(self.conf.upstream_l3_table)
        elif 'b' in dir:  # bidir
            return max(len(self.conf.upstream_l3_table),
                       len(self.conf.downstream_l3_table))
        else:
            raise ValueError

    def gen_pkt(self, pkt_idx):
        # NB.  In the uplink case, the traffic leaves Tester via its
        # uplink port and arrives at the downlink of the SUT.
        ip = self.l3_table[pkt_idx % len(self.l3_table)].ip
        p = Ether(dst=self.sut_mac) / IP(dst=ip)
        p = self.add_payload(p, self.args.pkt_size)
        return p


class GenPkt_mgw(GenPkt):

    def get_auto_pkt_num(self):
        return len(self.conf.users)

    def gen_pkt(self, pkt_idx):
        direction = '%s' % self.args.dir[0]
        pkt_size = self.args.pkt_size
        gw = self.conf.gw
        server = random.choice(self.conf.srvs)
        user = random.choice(self.conf.users)
        proto = random.choice([TCP, UDP])
        if 'd' == direction:
            return self.gen_dl_pkt(pkt_size, proto, gw, server, user)
        elif 'u' == direction:
            bst = self.conf.bsts[user.tun_end]
            return self.gen_ul_pkt(pkt_size, proto, gw, server, user, bst)

    def gen_dl_pkt(self, pkt_size, proto, gw, server, user):
        p = (
            Ether(dst=gw.mac) /
            IP(src=server.ip, dst=user.ip) /
            proto()
        )
        p = self.add_payload(p, self.args.pkt_size)
        return p

    def gen_ul_pkt(self, pkt_size, proto, gw, server, user, bst):
        p = (
            Ether(src=bst.mac, dst=gw.mac, type=0x0800) /
            IP(src=bst.ip, dst=gw.ip) /
            UDP(sport=4789, dport=4789) /
            VXLAN(vni=user.teid, flags=0x08) /
            Ether(dst=gw.mac, type=0x0800) /
            IP(src=user.ip, dst=server.ip) /
            proto()
        )
        p = self.add_payload(p, self.args.pkt_size)
        return p


class GenPkt_vmgw(GenPkt_mgw):
    def gen_pkt(self, pkt_idx):
        pkt = super(GenPkt_vmgw, self).gen_pkt(pkt_idx)
        # Add VXLAN header for infra processing
        vxlan_pkt = (
            Ether(src=self.conf.dcgw.mac, dst=self.conf.gw.mac) /
            IP(src=self.conf.dcgw.ip, dst=self.conf.gw.ip) /
            UDP(sport=4788, dport=4789) /
            VXLAN(vni=self.conf.dcgw.vni) /
            pkt
        )
        return vxlan_pkt


class GenPkt_bng(GenPkt):

    def get_auto_pkt_num(self):
        return len(self.conf.nat_table)

    def gen_pkt(self, pkt_idx):
        protos = {'6': TCP, '17': UDP}
        gw = self.conf.gw
        server = random.choice(self.conf.srvs)
        user = random.choice(self.conf.users)
        user_nat = random.choice([e for e in self.conf.nat_table
                                  if e.priv_ip == user.ip])
        proto = protos[str(user_nat.proto)]
        if 'd' in self.args.dir:
            pkt = (
                Ether(dst=gw.mac) /
                IP(src=server.ip, dst=user_nat.pub_ip) /
                proto(sport=user_nat.pub_port, dport=user_nat.pub_port)
            )
        elif 'u' in self.args.dir:
            cpe = self.conf.cpe[user.tun_end]
            pkt = (
                Ether(src=cpe.mac, dst=gw.mac, type=0x0800) /
                IP(src=cpe.ip, dst=gw.ip) /
                UDP(sport=4789, dport=4789) /
                VXLAN(vni=user.teid, flags=0x08) /
                Ether(dst=gw.mac, type=0x0800) /
                IP(src=user.ip, dst=server.ip) /
                proto(sport=user_nat.priv_port, dport=user_nat.priv_port)
            )
        else:
            raise ValueError
        pkt = self.add_payload(pkt, self.args.pkt_size)
        return pkt


class GenPkt_fw(GenPkt):
    def __init__(self, *args, **kw):
        super(GenPkt_fw, self).__init__(*args, **kw)
        self.args.auto_pkt_num = True

    def create_work_items(self, job_size):
        # Call `trace_generator` first
        args = self.args
        rulefile = 'fw_rules'
        tracefile = rulefile + '_trace'
        pareto_a = args.trace_generator_pareto_a
        pareto_b = args.trace_generator_pareto_b
        scale    = args.trace_generator_scale
        cmd = [args.trace_generator_cmd, pareto_a, pareto_b, scale, rulefile]
        cmd = [str(s) for s in cmd]

        print(' '.join(cmd))
        subprocess.check_call(cmd)
        with open(tracefile) as f:
            lines = f.readlines()
        self.args.pkt_num = len(lines)

        items = []
        for job_idx, ls in enumerate(grouper(job_size, lines)):
            ls = [l for l in ls if l is not None]
            items.append({'job_idx': job_idx, 'pkt_idxs': ls})
        return items

    def gen_pkt(self, line):
        def int2ip(ip):
            return socket.inet_ntoa(hex(ip)[2:].zfill(8).decode('hex'))
        vals = line.split('\t')
        src, dst, sport, dport, proto, a, b = [int(v) for v in vals]
        src = int2ip(src)
        dst = int2ip(dst)

        smac = byte_seq('aa:bb:bb:aa:%02x:%02x', random.randrange(1, 65023))
        dmac = byte_seq('aa:cc:dd:cc:%02x:%02x', random.randrange(1, 65023))
        p = Ether(dst=dmac, src=smac) / IP(dst=dst, src=src, proto=proto)
        if proto == 6:   # TCP
            p = p / TCP(sport=sport, dport=dport)
        if proto == 17:  # UDP
            p = p / UDP(sport=sport, dport=dport)
        p = self.add_payload(p, self.args.pkt_size)
        return p

def output_pkts(args, pkts):
    if args.ascii:
        for p in pkts:
            if sys.stdout.isatty():
                #scapy.config.conf.color_theme = themes.DefaultTheme()
                scapy.config.conf.color_theme = scapy.themes.ColorOnBlackTheme()
            print(p.__repr__())
    else:
        if args.auto_pkt_num and args.pkt_num < 1024:
            pkts = list(pkts)
            if pkts == []:
                exit(-1)
            while len(pkts) < 1024:
                pkts = pkts + pkts
        args.pcap_file.write(pkts)

def gen_pcap(*defaults):
    args = parse_args(defaults)
    conf = json_load(args.conf, object_hook=ObjectView)

    if args.random_seed:
        random.seed(args.random_seed)

    in_que = multiprocessing.Queue()
    out_que = multiprocessing.Queue()
    gen_pkt_class = globals()['GenPkt_%s' % conf.name]
    gen_pkt_obj = gen_pkt_class(args, conf, in_que, out_que)
    worker_num = max(1, args.thread)
    job_size = 1024

    if args.ascii:
        print("Dumping packets:")
    else:
        args.pcap_file = PcapWriter(args.output.name)

    processes = []
    for i in range(worker_num):
        p = multiprocessing.Process(target=gen_pkt_obj.do_work)
        p.start()
        processes.append(p)

    num_jobs = 0
    for item in gen_pkt_obj.create_work_items(job_size):
        in_que.put(item)
        num_jobs += 1

    results = []
    next_idx = 0
    while next_idx < num_jobs:
        result = out_que.get()
        if 'exception' in result:
            print('Exception: %s' % result['exception'])
            print(''.join(result['traceback']))
            exit()
        # print('idx: %s' % result['job_idx'])
        results.append(result)
        results.sort(key=lambda x: x['job_idx'])
        # print([x['job_idx'] for x in results])
        while len(results) > 0 and results[0]['job_idx'] == next_idx:
            # print('w: %s' % results[0]['job_idx'])
            pkts = [PicklablePacket.__call__(p) for p in results[0]['pkts']]
            output_pkts(args, pkts)
            results.pop(0)
            next_idx += 1

    # stop workers
    for i in range(worker_num):
        in_que.put(None)
    for p in processes:
        p.join()

    if not args.ascii:
        args.pcap_file.close()

def json_load(file, object_hook=None):
    if type(file) == str:
        with open(file, 'r') as infile:
            return json.load(infile, object_hook=object_hook)
    elif type(file) == PosixPath:
        with file.open('r') as infile:
            return json.load(infile, object_hook=object_hook)
    else:
        return json.load(file, object_hook=object_hook)

def parse_args(defaults=None):
    if defaults:
        required = False
    else:
        required = True
    parser = argparse.ArgumentParser()
    args_from_schema.add_args(parser, 'traffic')
    parser.formatter_class = argparse.ArgumentDefaultsHelpFormatter
    pa_args = None
    if defaults:
        parser.set_defaults(**defaults)
        pa_args = []
    args = parser.parse_args(pa_args)
    if args.json:
        new_defaults = json_load(args.json, ObjectView).as_dict()
        parser.set_defaults(**new_defaults)
        args = parser.parse_args(pa_args)
    if args.thread == 0:
        args.thread = multiprocessing.cpu_count()

    return args


if __name__ == "__main__":
    gen_pcap()
