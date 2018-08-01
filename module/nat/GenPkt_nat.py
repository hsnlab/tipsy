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

import random
from scapy.all import *

from gen_pcap_base import GenPkt as Base
from gen_pcap_base import byte_seq



class GenPkt(Base):

    def get_auto_pkt_num(self):
        #return self.conf.num_flows
        return 10

    def gen_pkt(self, pkt_idx):
        smac = 'aa:bb:bb:aa:ab:ba'
        dmac = 'aa:cc:dd:cc:ac:dc'
        sip = byte_seq('2.2.%d.%d', (pkt_idx % 64516) + 1)
        dip = byte_seq('3.3.%d.%d', (pkt_idx % 64516) + 1)
        p = Ether(dst=dmac, src=smac) / IP(src=sip, dst=dip)
        p = self.add_payload(p, self.args.pkt_size)
        return p
