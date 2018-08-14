# TIPSY: Telco pIPeline benchmarking SYstem
#
# Copyright (C) 2017-2018 by its authors (See AUTHORS)
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
#
# This file incorporates work covered by the following copyright and
# permission notice:
#
# Copyright (C) 2015 Nippon Telegraph and Telephone Corporation.
# Copyright (C) 2015 YAMAMOTO Takashi <yamamoto at valinux co jp>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import struct
from ryu import utils
from ryu.lib.pack_utils import msg_pack_into
from ryu.ofproto import ofproto_v1_3 as ofp
from ryu.ofproto import ofproto_v1_3_parser as ofpp


ERICSSON_EXPERIMENTER_ID = 0x00D0F0DB
EXP_ERI_PUSH_VXLAN = 0x00000000
EXP_ERI_POP_VXLAN  = 0x00000001

# This file is based on nx_actions.py, but for simplicity it avoids
# using the more general generate() method, and implements
# experimenter actions only for ofproto_v1_3.

# We cannot simply derive EricssonAction from NXAction becasue parse()
# and serialize() directly uses the _fmt_str:
# See NXAction._fmt_str and EricssonAction._fmt_str
class EricssonAction(ofpp.NXAction):
    _experimenter = ERICSSON_EXPERIMENTER_ID
    _fmt_str = '!I'  # subtype

    @classmethod
    def parse(cls, buf):
        fmt_str = EricssonAction._fmt_str
        (subtype,) = struct.unpack_from(fmt_str, buf, 0)
        subtype_cls = cls._subtypes.get(subtype)
        rest = buf[struct.calcsize(fmt_str):]
        if subtype_cls is None:
            return ofpp.NXActionUnknown(subtype, rest)
        return subtype_cls.parser(rest)

    def serialize(self, buf, offset):
        data = self.serialize_body()
        payload_offset = (
            ofp.OFP_ACTION_EXPERIMENTER_HEADER_SIZE +
            struct.calcsize(self._fmt_str)
        )
        self.len = utils.round_up(payload_offset + len(data), 8)
        super(ofpp.NXAction, self).serialize(buf, offset)
        msg_pack_into(EricssonAction._fmt_str,
                      buf,
                      offset + ofp.OFP_ACTION_EXPERIMENTER_HEADER_SIZE,
                      self.subtype)
        buf += data


class EricssonActionPushVXLAN(EricssonAction):
    """
    PUSH_VXLAN  = 0x00000000  vni (32 bits)
    """
    _subtype = EXP_ERI_PUSH_VXLAN
    _fmt_str = '!2xI'           # VNI

    def __init__(self, vni,
                 type_=None, len_=None, vendor=None, subtype=None):
        super(EricssonActionPushVXLAN, self).__init__()
        self.vni = vni

    @classmethod
    def parser(cls, buf):
        (vni,) = struct.unpack_from(cls._fmt_str, buf, 0)
        return cls(vni)

    def serialize_body(self):
        data = bytearray()
        msg_pack_into(self._fmt_str, data, 0, self.vni)
        return data

class EricssonActionPopVXLAN(EricssonAction):
    """
    POP_VXLAN   = 0x00000001
    """
    _subtype = EXP_ERI_POP_VXLAN
    _fmt_str = '!4x'

    def __init__(self,
                 type_=None, len_=None, experimenter=None, subtype=None):
        super(EricssonActionPopVXLAN, self).__init__()

    @classmethod
    def parser(cls, buf):
        return cls()

    def serialize_body(self):
        data = bytearray()
        msg_pack_into(self._fmt_str, data, 0)
        return data

def register(ofpp):
    EricssonAction.__module__ = ofpp.__name__
    setattr(ofpp, 'EricssonAction', EricssonAction)
    EricssonAction.register(EricssonActionPushVXLAN)
    EricssonAction.register(EricssonActionPopVXLAN)
