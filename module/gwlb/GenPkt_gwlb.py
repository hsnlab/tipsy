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

import random
from scapy.all import *

from gen_pcap_base import GenPkt as Base

class GenPkt(Base):

    def get_auto_pkt_num(self):
        services = len(self.conf.service)
        backends = len(self.conf.service[0].backend)
        # 10 pkts for each backend
        return 10 * services * backends

    def gen_pkt(self, pkt_idx):
        services = len(self.conf.service)
        backends = len(self.conf.service[0].backend)

        service_idx = (pkt_idx // backends) % services
        backend_idx = pkt_idx % backends
        service = self.conf.service[service_idx]
        backend = service.backend[backend_idx]

        if backend.prefix_len > 24:
            raise Exception('prefix (%d) > 24' % backend.prefix_len)
        ip_src = backend.ip_src.split('.')
        ip_src[-1] = str(random.randint(1, 254))
        ip_src = '.'.join(ip_src)
        udp_src = 22 + (pkt_idx % 1000)

        pkt = (
            Ether(dst=self.conf.gw.mac) /
            IP(src=ip_src, dst=service.ip_dst) /
            UDP(sport=udp_src, dport=int(service.udp_dst))
        )

        pkt = self.add_payload(pkt, self.args.pkt_size)
        return pkt

