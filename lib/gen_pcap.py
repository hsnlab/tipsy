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

import argparse
import json
import multiprocessing
import random
import scapy
import sys
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
    def __init__(self, **kwargs):
        tmp = {k.replace('-', '_'): v for k, v in kwargs.items()}
        self.__dict__.update(**tmp)

    def __repr__(self):
        return self.__dict__.__repr__()

    def as_dict(self):
        return self.__dict__


def byte_seq(template, seq):
    return template % (int(seq / 254), (seq % 254) + 1)


def gen_packets(params_tuple):
    (dir, pkt_num, pkt_size, conf) = params_tuple
    pl = conf.name
    pkts = []
    if dir[0] == 'b':
        dir = ('dl', 'ul')
    else:
        dir = (dir, dir)
    for i in range(pkt_num // 2 + 1):
        for d in dir:
            pkt_gen_func = getattr(sys.modules[__name__],
                                   '_gen_%s_pkt_%s' % (d, pl))
            p = pkt_gen_func(pkt_size, conf)
            pkts.append(PicklablePacket(p))
    return pkts[:pkt_num]


def _gen_pkt_portfwd(pkt_size, conf):
    smac = byte_seq('aa:bb:bb:aa:%02x:%02x', random.randrange(1, 65023))
    dmac = byte_seq('aa:cc:dd:cc:%02x:%02x', random.randrange(1, 65023))
    p = Ether(dst=dmac, src=smac)
    p = add_payload(p, pkt_size)
    return p


def _gen_dl_pkt_portfwd(pkt_size, conf):
    return _gen_pkt_portfwd(pkt_size, conf)


def _gen_ul_pkt_portfwd(pkt_size, conf):
    return _gen_pkt_portfwd(pkt_size, conf)


def _gen_pkt_l2fwd(dir, pkt_size, conf):
    smac = byte_seq('aa:bb:bb:aa:%02x:%02x', random.randrange(1, 65023))
    dmac = random.choice(getattr(conf, '%s_table' % dir)).mac
    p = Ether(dst=dmac, src=smac)
    p = add_payload(p, pkt_size)
    return p


def _gen_dl_pkt_l2fwd(pkt_size, conf):
    return _gen_pkt_l2fwd('downstream', pkt_size, conf)


def _gen_ul_pkt_l2fwd(pkt_size, conf):
    return _gen_pkt_l2fwd('upstream', pkt_size, conf)


def _gen_pkt_l3fwd(dir, pkt_size, conf):
    mac = getattr(conf.sut, '%sl_port_mac' % dir[0])
    ip = random.choice(getattr(conf, '%s_l3_table' % dir)).ip
    p = Ether(dst=mac) / IP(dst=ip)
    p = add_payload(p, pkt_size)
    return p


def _gen_dl_pkt_l3fwd(pkt_size, conf):
    return _gen_pkt_l3fwd('downstream', pkt_size, conf)


def _gen_ul_pkt_l3fwd(pkt_size, conf):
    return _gen_pkt_l3fwd('upstream', pkt_size, conf)


def _gen_dl_pkt_mgw(pkt_size, conf):
    server = random.choice(conf.srvs)
    user = random.choice(conf.users)
    proto = random.choice([TCP(), UDP()])
    p = (
        Ether(dst=conf.gw.mac) /
        IP(src=server.ip, dst=user.ip) /
        proto
    )
    p = add_payload(p, pkt_size)
    return p


def _gen_dl_pkt_vmgw(pkt_size, conf):
    p = _gen_dl_pkt_mgw(pkt_size, conf)
    p = vwrap(p, conf)
    return p


def _gen_dl_pkt_bng(pkt_size, conf):
    server = random.choice(conf.srvs)
    user = random.choice(conf.users)
    proto = random.choice([TCP(), UDP()])
    p = (
        Ether(dst=conf.gw.mac) /
        IP(src=server.ip, dst=user.ip) /
        proto
    )
    p = add_payload(p, pkt_size)
    return p


def _gen_ul_pkt_mgw(pkt_size, conf):
    server = random.choice(conf.srvs)
    user = random.choice(conf.users)
    proto = random.choice([TCP(), UDP()])
    bst = conf.bsts[user.tun_end]
    p = (
        Ether(src=bst.mac, dst=conf.gw.mac, type=0x0800) /
        IP(src=bst.ip, dst=conf.gw.ip) /
        UDP(sport=4789, dport=4789) /
        VXLAN(vni=user.teid, flags=0x08) /
        Ether(dst=conf.gw.mac, type=0x0800) /
        IP(src=user.ip, dst=server.ip) /
        proto
    )
    p = add_payload(p, pkt_size)
    return p


def _gen_ul_pkt_vmgw(pkt_size, conf):
    p = _gen_ul_pkt_mgw(pkt_size, conf)
    p = vwrap(p, conf)
    return p


def _gen_ul_pkt_bng(pkt_size, conf):
    server = random.choice(conf.srvs)
    user = random.choice(conf.users)
    proto = random.choice([TCP(), UDP()])
    cpe = conf.cpe[user.tun_end]
    p = (
        Ether(src=cpe.mac, dst=conf.gw.mac, type=0x0800) /
        IP(src=cpe.ip, dst=conf.gw.ip) /
        UDP(sport=4789, dport=4789) /
        VXLAN(vni=user.teid, flags=0x08) /
        Ether(dst=conf.gw.mac, type=0x0800) /
        IP(src=user.ip, dst=server.ip) /
        proto
    )
    p = add_payload(p, pkt_size)
    return p


def add_payload(p, pkt_size):
    if len(p) < pkt_size:
        #"\x00" is a single zero byte
        s = "\x00" * (pkt_size - len(p))
        p = p / Padding(s)
    return p


def vwrap(pkt, conf):
    'Add VXLAN header for infra processing in case of a virtual mgw.'
    vxlanpkt = (
        Ether(src=conf.dcgw.mac, dst=conf.gw.mac) /
        IP(src=conf.dcgw.ip, dst=conf.gw.ip) /
        UDP(sport=4788, dport=4789) /
        VXLAN(vni=conf.dcgw.vni) /
        pkt
    )
    return vxlanpkt


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
        new_defaults = json_load(args.json,
                                 lambda x: ObjectView(**x)).as_dict()
        parser.set_defaults(**new_defaults)
        args = parser.parse_args(pa_args)
    if args.thread == 0:
        args.thread = multiprocessing.cpu_count()

    return args


def gen_pcap(defaults=None):
    args = parse_args(defaults)
    conf = json_load(args.conf, object_hook=lambda x: ObjectView(**x))

    if args.random_seed:
        random.seed(args.random_seed)

    dir = '%sl' % args.dir[0]
    wargs = []
    worker_num = min(args.pkt_num, args.thread)
    pkt_left = args.pkt_num
    ppw = args.pkt_num // worker_num
    for _ in range(worker_num):
        wargs.append((dir, ppw, args.pkt_size, conf))
        pkt_left -= ppw
    if pkt_left > 0:
        wargs.append((dir, args.pkt_num % worker_num, args.pkt_size, conf))
        worker_num += 1
    workers = multiprocessing.Pool(worker_num)

    pkts = workers.map(gen_packets, wargs)
    pkts = [p for wpkts in pkts for p in wpkts]
    pkts = map(PicklablePacket.__call__, pkts)

    if args.ascii:
        print("Dumping packets:")
        for p in pkts:
            if sys.stdout.isatty():
                #scapy.config.conf.color_theme = themes.DefaultTheme()
                scapy.config.conf.color_theme = themes.ColorOnBlackTheme()
            # p.show()
            print(p.__repr__())
    else:
        wrpcap(args.output, pkts)


if __name__ == "__main__":
    gen_pcap()
