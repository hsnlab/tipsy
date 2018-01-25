#!/usr/bin/env python
import argparse
import itertools
import json
import multiprocessing
import random
import scapy
try:
    from pathlib import PosixPath
except ImportError:
    # python2
    PosixPath = str
from scapy.all import *

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
        self.__dict__.update(kwargs)

    def __repr__(self):
        return self.__dict__.__repr__()


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
    proto = random.choice([TCP(), UDP()])
    cpe = conf.cpe[user.tun_end_id]
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
    parser.add_argument('--json', '-j', type=argparse.FileType('r'),
                        help='Input config file, '
                        'command line arguments override settings')
    parser.add_argument('--conf', '-c', type=argparse.FileType('r'),
                        help='Measurement setup (in JSON)', required=required)
    parser.add_argument('--output', '-o', type=argparse.FileType('w'),
                        help='Output file', default='/dev/stdout')
    parser.add_argument('--dir', '-d', type=str,
                        help='Direction: uplink, downlink or bidir',
                        default='uplink')
    parser.add_argument('--pkt-num', '-n', type=int,
                        help='Number of packets',
                        default=10)
    parser.add_argument('--pkt-size', '-s', type=int,
                        help='Size of packets',
                        default=64)
    parser.add_argument('--thread', '-t', type=int,
                        help='Number of requested processing CPU threads. '
                        '0 means all of the available cores.',
                        default=0)
    parser.add_argument('--ascii', '-a',
                        help='Dump generated packets in human readable ASCII form',
                        action='store_true')
    parser.set_defaults(ascii=False)
    parser.formatter_class = argparse.ArgumentDefaultsHelpFormatter
    pa_args = None
    if defaults:
        parser.set_defaults(**defaults)
        pa_args = []
    args = parser.parse_args(pa_args)
    if args.json:
        new_defaults = json_load(args.json)
        parser.set_defaults(**new_defaults)
        args = parser.parse_args(pa_args)
    if args.thread == 0:
        args.thread = multiprocessing.cpu_count()

    return args


def gen_pcap(defaults=None):
    args = parse_args(defaults)
    conf = json_load(args.conf, object_hook=lambda x: ObjectView(**x))

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
