# TIPSY: Telco pIPeline benchmarking SYstem
#
# Copyright (C) 2023 by its authors (See AUTHORS)
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
        return 0

    def get_pkt_num(self):
        return 0

    def gen_pkt(self, pkt_idx):
        pkt = ( Ether() / IP() / UDP() )
        return pkt

