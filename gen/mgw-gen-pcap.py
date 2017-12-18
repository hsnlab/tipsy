#!/usr/bin/env python
from scapy.all import *
import argparse
import json
import itertools
import multiprocessing
import random


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


def downlink(params_tuple):
    (pkt_num, pkt_size, conf) = params_tuple
    pkts = []
    for i in range(pkt_num):
        server = random.choice(conf.srvs)
        user = random.choice(conf.users)
        proto = random.choice([TCP(), UDP()])
        p = (
            Ether(dst=conf.gw.mac) /
            IP(src=server.ip, dst=user.ip) /
            proto
        )
        p = add_payload(p, pkt_size)
        p = vwrap(p, conf)
        pkts.append(PicklablePacket(p))
    return pkts


def uplink(params_tuple):
    (pkt_num, pkt_size, conf) = params_tuple
    pkts = []
    for i in range(pkt_num):
        server = random.choice(conf.srvs)
        user = random.choice(conf.users)
        proto = random.choice([TCP(), UDP()])
        bst = conf.bsts[user.bst]
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
        p = vwrap(p, conf)
        pkts.append(PicklablePacket(p))
    return pkts


def add_payload(p, pkt_size):
    if len(p) < pkt_size:
        #"\x00" is a single zero byte
        s = "\x00" * (pkt_size - len(p))
        p = p / Padding(s)
    return p


def vwrap(pkt, conf):
    'Add VXLAN header for infra processing in case of a virtual mgw.'
    if not conf.virtual_mgw:
        return pkt
    vxlanpkt = (
        Ether(src=conf.dcgw.mac, dst=conf.gw.mac) /
        IP(src=conf.dcgw.ip, dst=conf.gw.ip) /
        UDP(sport=4788, dport=4789) /
        VXLAN(vni=conf.dcgw.vni) /
        pkt
    )
    return vxlanpkt


##################
parser = argparse.ArgumentParser()
parser.add_argument('--json', '-j', type=argparse.FileType('r'),
                    help='Input config file, '
                    'command line arguments override settings')
parser.add_argument('--conf', '-c', type=argparse.FileType('r'),
                    help='Measurement setup (in JSON)',  required=True)
parser.add_argument('--output', '-o', type=str,
                    help='Output file',
                    default='/dev/stdout')
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
                    help='Number of requested processing CPU threads',
                    default=4)
parser.add_argument('--ascii', '-a',
                    help='Dump generated packets in human readable ASCII form',
                    action='store_true')
parser.set_defaults(ascii=False)
args = parser.parse_args()
if args.json:
    new_defaults = json.load(args.json)
    parser.set_defaults(**new_defaults)
    args = parser.parse_args()


def conv_fn(d): return ObjectView(**d)
conf = json.load(args.conf, object_hook=conv_fn)

wargs = []
worker_num = min(args.pkt_num, args.thread)
pkt_left = args.pkt_num
ppw = args.pkt_num / worker_num
for _ in range(worker_num):
    wargs.append((ppw, args.pkt_size, conf))
    pkt_left -= ppw
if pkt_left > 0:
    wargs.append((args.pkt_num % worker_num, args.pkt_size, conf))
    worker_num += 1
workers = multiprocessing.Pool(worker_num)

if args.dir == 'uplink' or args.dir == 'u':
    pkts = workers.map(uplink, wargs)
    pkts = [p for wpkts in pkts for p in wpkts]  # flatten pkts
elif args.dir == 'downlink' or args.dir == 'd':
    pkts = workers.map(downlink, wargs)
    pkts = [p for wpkts in pkts for p in wpkts]
elif args.dir == 'bidir' or args.dir == 'b':
    upkts = workers.map(uplink, wargs)
    dpkts = workers.map(downlink, wargs)
    upkts = [p for wpkts in upkts for p in wpkts]
    dpkts = [p for wpkts in dpkts for p in wpkts]
    pkts = list(itertools.chain(*zip(upkts, dpkts)))[:args.pkt_num]
else:
    raise RuntimeError("Unknown direction: %s" % args.dir)

pkts = map(PicklablePacket.__call__, pkts)

if args.ascii:
    print("Dumping packets:")
    for p in pkts:
        # p.show()
        print(p.__repr__())
else:
    wrpcap(args.output, pkts)
