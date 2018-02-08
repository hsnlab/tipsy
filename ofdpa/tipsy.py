#!/usr/bin/env python2

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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.    See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
"""
TIPSY controller for OFDPA pipeline
Run as:

   $ ./tipsy.py --log-config-file path/to/log.cfg

This implementation of TIPSY is specific to configure hardware
switches having the OpenFlow Data Plane Abstraction (OF-DPA) API.
More on OF-DPA:

   https://www.broadcom.com/products/ethernet-connectivity/software/of-dpa

Compatible hardware switches using Open Network Linux are listed here:

   http://opennetlinux.org/hcl

Tested on Edge-Core AS4610-54T
"""

import json
import os
import requests
import signal
import socket
import struct
import subprocess
import sys

sys.path.append('/usr/bin')
from OFDPA_python import *

conf_file = '/tmp/pipeline.json'
webhook_configured = 'http://localhost:9000/configured'
client_purge = '/usr/bin/client_cfg_purge'

###########################################################################


class ObjectView(object):
    def __init__(self, **kwargs):
        kw = {k.replace('-', '_'): v for k, v in kwargs.items()}
        self.__dict__.update(kw)

    def __repr__(self):
        return self.__dict__.__repr__()

    def get (self, attr, default=None):
        return self.__dict__.get(attr, default)


class PL(object):
    def __init__(self, parent, conf):
        self.conf = conf
        self.parent = parent
        self.has_tunnels = False

        self.ul_vlan_id = 10 # this VLAN denotes packets from ul_port
        self.dl_vlan_id = 20 # this VLAN denotes packets from dl_port


    def set_vlan_table(self):
        # Creating VLAN table entries.  (We must push vlan tags to
        # incoming packets, because the default action is 'drop'.)

        ul_vlan_id = self.ul_vlan_id
        dl_vlan_id = self.dl_vlan_id
        ul_port = self.parent.ul_port
        dl_port = self.parent.dl_port

        fe = ofdpaFlowEntry_t()
        ofdpaFlowEntryInit(OFDPA_FLOW_TABLE_ID_VLAN, fe)
        fe.flowData.vlanFlowEntry.gotoTableId = OFDPA_FLOW_TABLE_ID_TERMINATION_MAC
        mc = fe.flowData.vlanFlowEntry.match_criteria
        mc.inPort = ul_port
        mc.vlanId = (OFDPA_VID_PRESENT | ul_vlan_id)
        mc.vlanIdMask = (OFDPA_VID_PRESENT | OFDPA_VID_EXACT_MASK)
        ofdpaFlowAdd(fe)

        fe = ofdpaFlowEntry_t()
        ofdpaFlowEntryInit(OFDPA_FLOW_TABLE_ID_VLAN, fe)
        fe.flowData.vlanFlowEntry.gotoTableId = OFDPA_FLOW_TABLE_ID_TERMINATION_MAC
        mc = fe.flowData.vlanFlowEntry.match_criteria
        mc.inPort = dl_port
        mc.vlanId = (OFDPA_VID_PRESENT | dl_vlan_id)
        mc.vlanIdMask = (OFDPA_VID_PRESENT | OFDPA_VID_EXACT_MASK)
        ofdpaFlowAdd(fe)

        # Create entries to accept packets with no VLAN header and push VLAN header
        fe = ofdpaFlowEntry_t()
        ofdpaFlowEntryInit(OFDPA_FLOW_TABLE_ID_VLAN, fe)
        mc = fe.flowData.vlanFlowEntry.match_criteria
        mc.vlanId = 0 # There is no VLAN present
        mc.inPort = ul_port # Packet came from ul_port
        mc.vlanIdMask = (OFDPA_VID_PRESENT | OFDPA_VID_EXACT_MASK)
        fe.flowData.vlanFlowEntry.gotoTableId = OFDPA_FLOW_TABLE_ID_TERMINATION_MAC
        fe.flowData.vlanFlowEntry.setVlanIdAction = 1 # Set VLAN aka push a header
        fe.flowData.vlanFlowEntry.newVlanId = (OFDPA_VID_PRESENT | ul_vlan_id)
        ofdpaFlowAdd(fe)

        fe = ofdpaFlowEntry_t()
        ofdpaFlowEntryInit(OFDPA_FLOW_TABLE_ID_VLAN, fe)
        mc = fe.flowData.vlanFlowEntry.match_criteria
        mc.vlanId = 0  # There is no VLAN present
        mc.inPort = dl_port  # packet came from ul_port
        mc.vlanIdMask = (OFDPA_VID_PRESENT | OFDPA_VID_EXACT_MASK)
        fe.flowData.vlanFlowEntry.gotoTableId = OFDPA_FLOW_TABLE_ID_TERMINATION_MAC
        fe.flowData.vlanFlowEntry.setVlanIdAction = 1  # Set VLAN aka push a header
        fe.flowData.vlanFlowEntry.newVlanId = (OFDPA_VID_PRESENT | dl_vlan_id)
        ofdpaFlowAdd(fe)

    def get_tunnel_endpoints(self):
        raise NotImplementedError

    def do_unknown(self, action):
        print('Unknown action: %s' % action.action)


class PL_portfwd(PL):
    """L2 Port Forwarding

    In the upstream direction the pipeline will receive L2 packets from the
    downlink port of the SUT and forward them to the uplink port. Meanwhile, it
    may optionally rewrite the source MAC address in the L2 frame to the MAC
    address of the uplink port (must be specified by the pipeline config).    The
    downstream direction is the same, but packets are received from the uplink
    port and forwarded to the downlink port after an optional MAC rewrite.
    """
    def __init__(self, parent, conf):
        super(PL_portfwd, self).__init__(parent, conf)

    def config_switch(self):
        ul_port = self.parent.ul_port
        dl_port = self.parent.dl_port

        rc = ofdpaClientInitialize("TIPSY Port Forward")
        if rc == OFDPA_E_NONE:
            dummy_vlan = 10 # Dummy VLAN for L2 groups
            mac = self.conf.mac_swap_downstream

            # Creating L2 interface group entry
            group_id = new_uint32_tp()
            uint32_tp_assign(group_id, 0)
            ifgroup_entry = ofdpaGroupEntry_t()
            ifgroup_bucket = ofdpaGroupBucketEntry_t()
            ofdpaGroupTypeSet(group_id, OFDPA_GROUP_ENTRY_TYPE_L2_INTERFACE)
            ofdpaGroupVlanSet(group_id, dummy_vlan)
            ofdpaGroupPortIdSet(group_id, ul_port)
            ifgroup_entry.groupId = uint32_tp_value(group_id)
            ifgroup_bucket.groupId = ifgroup_entry.groupId
            ifgroup_bucket.bucketIndex = 0
            ifgroup_bucket.bucketData.l2Interface.outputPort = ul_port
            ifgroup_bucket.bucketData.l2Interface.popVlanTag = 1
            ofdpaGroupAdd(ifgroup_entry)
            ofdpaGroupBucketEntryAdd(ifgroup_bucket)

            if mac:
                # Creating L2 rewire group
                group_id = new_uint32_tp()
                uint32_tp_assign(group_id, 0)
                rewrite_group_entry = ofdpaGroupEntry_t()
                rewrite_group_bucket = ofdpaGroupBucketEntry_t()
                ofdpaGroupTypeSet(group_id, OFDPA_GROUP_ENTRY_TYPE_L2_REWRITE)
                ofdpaGroupIndexSet(group_id, 1)
                rewrite_group_entry.groupId = uint32_tp_value(group_id)
                rewrite_group_bucket.groupId = rewrite_group_entry.groupId
                # Refers to L2 interface group:
                rewrite_group_bucket.referenceGroupId = ifgroup_entry.groupId
                rewrite_group_bucket.bucketIndex = 0
                MACAddress_set(rewrite_group_bucket.bucketData.l2Rewrite.dstMac,
                               str(mac))
                ofdpaGroupAdd(rewrite_group_entry)
                ofdpaGroupBucketEntryAdd(rewrite_group_bucket)

            # Creating entry in ACL table where in_port=dl_port,
            # action=group:rewrite_group_entry.groupId
            if mac:
                gr_id = rewrite_group_entry.groupId
            else:
                gr_id = ifgroup_entry.groupId
            acl_flow_entry = ofdpaFlowEntry_t()
            ofdpaFlowEntryInit(OFDPA_FLOW_TABLE_ID_ACL_POLICY, acl_flow_entry)
            mc = acl_flow_entry.flowData.policyAclFlowEntry.match_criteria
            mc.inPort = dl_port
            mc.inPortMask = OFDPA_INPORT_EXACT_MASK
            mc.etherTypeMask = OFDPA_ETHERTYPE_ALL_MASK
            acl_flow_entry.flowData.policyAclFlowEntry.groupID = gr_id
            ofdpaFlowAdd(acl_flow_entry)

            # Creating L2 interface group entry
            group_id = new_uint32_tp()
            uint32_tp_assign(group_id, 0)
            ifgroup_entry = ofdpaGroupEntry_t()
            ifgroup_bucket = ofdpaGroupBucketEntry_t()
            ofdpaGroupTypeSet(group_id, OFDPA_GROUP_ENTRY_TYPE_L2_INTERFACE)
            ofdpaGroupVlanSet(group_id, dummy_vlan)
            ofdpaGroupPortIdSet(group_id, dl_port)
            ifgroup_entry.groupId = uint32_tp_value(group_id)
            ifgroup_bucket.groupId = ifgroup_entry.groupId
            ifgroup_bucket.bucketIndex = 0
            ifgroup_bucket.bucketData.l2Interface.outputPort = dl_port
            ifgroup_bucket.bucketData.l2Interface.popVlanTag = 1
            ofdpaGroupAdd(ifgroup_entry)
            ofdpaGroupBucketEntryAdd(ifgroup_bucket)

            mac = self.conf.mac_swap_upstream
            if mac:
                # Creating L2 rewire group
                group_id = new_uint32_tp()
                uint32_tp_assign(group_id, 0)
                rewrite_group_entry = ofdpaGroupEntry_t()
                rewrite_group_bucket = ofdpaGroupBucketEntry_t()
                ofdpaGroupTypeSet(group_id, OFDPA_GROUP_ENTRY_TYPE_L2_REWRITE)
                ofdpaGroupIndexSet(group_id, 2)
                rewrite_group_entry.groupId = uint32_tp_value(group_id)
                rewrite_group_bucket.groupId = rewrite_group_entry.groupId
                # Refers to L2 interface group:
                rewrite_group_bucket.referenceGroupId = ifgroup_entry.groupId
                rewrite_group_bucket.bucketIndex = 0
                MACAddress_set(rewrite_group_bucket.bucketData.l2Rewrite.dstMac,
                               str(mac))
                ofdpaGroupAdd(rewrite_group_entry)
                ofdpaGroupBucketEntryAdd(rewrite_group_bucket)

            if mac:
                gr_id = rewrite_group_entry.groupId
            else:
                gr_id = ifgroup_entry.groupId
            # Creating entry in ACL table where in_port=ul_port,
            # action=group:rewrite_group_entry.groupId
            acl_flow_entry = ofdpaFlowEntry_t()
            ofdpaFlowEntryInit(OFDPA_FLOW_TABLE_ID_ACL_POLICY, acl_flow_entry)
            mc = acl_flow_entry.flowData.policyAclFlowEntry.match_criteria
            mc.inPort = ul_port
            mc.inPortMask = OFDPA_INPORT_EXACT_MASK
            mc.etherTypeMask = OFDPA_ETHERTYPE_ALL_MASK
            acl_flow_entry.flowData.policyAclFlowEntry.groupID = gr_id
            ofdpaFlowAdd(acl_flow_entry)


class PL_l2fwd(PL):
    """L2 Packet Forwarding

    Upstream the L2fwd pipeline will receive packets from the downlink
    port, perform a lookup for the destination MAC address in a static
    MAC table, and if a match is found the packet will be forwarded to
    the uplink port or otherwise dropped (or likewise forwarded upstream
    if the =fakedrop= parameter is set to =true=).    The downstream
    pipeline is just the other way around, but note that the upstream
    and downstream pipelines use separate MAC tables.
    """

    def __init__(self, parent, conf):
        super(PL_l2fwd, self).__init__(parent, conf)

    def config_switch(self):
        ul_port = self.parent.ul_port
        dl_port = self.parent.dl_port

        # Initialize OFDPA API connection
        rc = ofdpaClientInitialize("TIPSY L2 Bridging")
        if rc == OFDPA_E_NONE:

            # Creating L2 Interface Group Entry for both dl_port and ul_port
            group_id = new_uint32_tp()
            uint32_tp_assign(group_id, 0)
            l2_ifgroup_entry_ul = ofdpaGroupEntry_t()
            l2_ifgroup_bucket_ul = ofdpaGroupBucketEntry_t()
            ofdpaGroupTypeSet(group_id, OFDPA_GROUP_ENTRY_TYPE_L2_INTERFACE)
            ofdpaGroupVlanSet(group_id, dl_vlan_id) # Refers to in_port=dl_port
            ofdpaGroupPortIdSet(group_id, ul_port)
            l2_ifgroup_entry_ul.groupId = uint32_tp_value(group_id)
            l2_ifgroup_bucket_ul.groupId = l2_ifgroup_entry_ul.groupId
            l2_ifgroup_bucket_ul.bucketIndex = 0
            l2_ifgroup_bucket_ul.bucketData.l2Interface.outputPort = ul_port
            l2_ifgroup_bucket_ul.bucketData.l2Interface.popVlanTag = 1
            ofdpaGroupAdd(l2_ifgroup_entry_ul)
            ofdpaGroupBucketEntryAdd(l2_ifgroup_bucket_ul)

            group_id = new_uint32_tp()
            uint32_tp_assign(group_id, 0)
            l2_ifgroup_entry_dl = ofdpaGroupEntry_t()
            l2_ifgroup_bucket_dl = ofdpaGroupBucketEntry_t()
            ofdpaGroupTypeSet(group_id, OFDPA_GROUP_ENTRY_TYPE_L2_INTERFACE)
            ofdpaGroupVlanSet(group_id, ul_vlan_id) # Refers to in_port=ul_port
            ofdpaGroupPortIdSet(group_id, ul_port)
            l2_ifgroup_entry_dl.groupId = uint32_tp_value(group_id)
            l2_ifgroup_bucket_dl.groupId = l2_ifgroup_entry_dl.groupId
            l2_ifgroup_bucket_dl.bucketIndex = 0
            l2_ifgroup_bucket_dl.bucketData.l2Interface.outputPort = dl_port
            l2_ifgroup_bucket_dl.bucketData.l2Interface.popVlanTag = 1
            ofdpaGroupAdd(l2_ifgroup_entry_dl)
            ofdpaGroupBucketEntryAdd(l2_ifgroup_bucket_dl)

            self.set_vlan_table()

            # Upstream flow rules which came from dl_port thus VLAN is
            # dl_vlan_id, and must go to output:ul_port thus
            # l2_ifgroup_entry_ul
            for entry in self.conf.upstream_table:
                self.mod_table('add', dl_vlan_id, l2_ifgroup_entry_ul.groupId, entry)

            # Same in downstream direction
            for entry in self.conf.downstream_table:
                self.mod_table('add', ul_vlan_id, l2_ifgroup_entry_dl.groupId, entry)

    def mod_table(self, cmd, vlan_id, group_id, entry):
        # Create bridge flow table entry in table 50
        #
        # vlan should mark the in_port,
        #    thus where vlan=ul_vlan_id -->
        #                    in_port=ul_port  -->
        #                    output: group=l2_ifgroup_entry_dl
        #
        #               vlan=dl_vlan_id -->
        #                    in_port=dl_port -->
        #                    output: group=l2_ifgroup_entry_ul
        fe = ofdpaFlowEntry_t()
        ofdpaFlowEntryInit(OFDPA_FLOW_TABLE_ID_BRIDGING, fe)
        bfe = fe.flowData.bridgingFlowEntry
        bfe.gotoTableId = OFDPA_FLOW_TABLE_ID_ACL_POLICY
        bfe.groupID = group_id
        bfe.match_criteria.vlanId = (OFDPA_VID_PRESENT | vlan_id)
        bfe.match_criteria.vlanIdMask = (OFDPA_VID_PRESENT | OFDPA_VID_EXACT_MASK)
        MACAddress_set(bfe.match_criteria.destMac, str(entry.mac))
        MACAddress_set(bfe.match_criteria.destMacMask, "ff:ff:ff:ff:ff:ff")
        ofdpaFlowAdd(fe)


class PL_l3fwd(PL):

    def __init__(self, parent, conf):
        super(PL_l3fwd, self).__init__(parent, conf)
        self.gr_next = 0
        self.gr_table = {}

    def add_gr_entry(self, entry, idx, l2_gr_id, vlan_id):
        ge = ofdpaGroupEntry_t()
        gr_id = new_uint32_tp()
        ofdpaGroupTypeSet(gr_id, OFDPA_GROUP_ENTRY_TYPE_L3_UNICAST)
        ofdpaGroupIndexSet(gr_id, idx)
        ge.groupId = uint32_tp_value(gr_id)
        ofdpaGroupAdd(ge)

        gb = ofdpaGroupBucketEntry_t()
        gb.groupId = ge.groupId
        gb.referenceGroupId = l2_gr_id
        MACAddress_set(gb.bucketData.l3Unicast.srcMac, str(entry.smac))
        MACAddress_set(gb.bucketData.l3Unicast.dstMac, str(entry.dmac))
        gb.bucketData.l3Unicast.vlanId = (vlan_id | OFDPA_VID_PRESENT)
        ofdpaGroupBucketEntryAdd(gb)

        return ge.groupId

    def config_switch(self):
        ul_port = self.parent.ul_port
        dl_port = self.parent.dl_port

        # A basic MAC table lookup to check that the L2 header of the
        # receiver packet contains the router's own MAC address(es) in
        # Termination MAC Flow Table (20)
        #
        # Than L3 addresses go to Routing Table (30) where group action
        # must refer to a next hop group a.k.a. L3 Unicast Group Entry

        # Connect to OFDPA API
        rc = ofdpaClientInitialize("TIPSY L3 Routing")
        if rc == OFDPA_E_NONE:

            ul_vlan_id = 10 # this VLAN denotes packets from ul_port
            dl_vlan_id = 20 # this VLAN denotes packets from dl_port

            # Creating L2 Interface Group Entry for both dl_port and ul_port
            group_id = new_uint32_tp()
            uint32_tp_assign(group_id, 0)
            l2_ifgroup_entry_ul = ofdpaGroupEntry_t()
            l2_ifgroup_bucket_ul = ofdpaGroupBucketEntry_t()
            ofdpaGroupTypeSet(group_id, OFDPA_GROUP_ENTRY_TYPE_L2_INTERFACE)
            ofdpaGroupVlanSet(group_id, dl_vlan_id) # Refers to in_port=dl_port
            ofdpaGroupPortIdSet(group_id, ul_port)
            l2_ifgroup_entry_ul.groupId = uint32_tp_value(group_id)
            l2_ifgroup_bucket_ul.groupId = l2_ifgroup_entry_ul.groupId
            l2_ifgroup_bucket_ul.bucketIndex = 0
            l2_ifgroup_bucket_ul.bucketData.l2Interface.outputPort = ul_port
            l2_ifgroup_bucket_ul.bucketData.l2Interface.popVlanTag = 1
            ofdpaGroupAdd(l2_ifgroup_entry_ul)
            ofdpaGroupBucketEntryAdd(l2_ifgroup_bucket_ul)

            group_id = new_uint32_tp()
            uint32_tp_assign(group_id, 0)
            l2_ifgroup_entry_dl = ofdpaGroupEntry_t()
            l2_ifgroup_bucket_dl = ofdpaGroupBucketEntry_t()
            ofdpaGroupTypeSet(group_id, OFDPA_GROUP_ENTRY_TYPE_L2_INTERFACE)
            ofdpaGroupVlanSet(group_id, ul_vlan_id) # Refers to in_port=ul_port
            ofdpaGroupPortIdSet(group_id, dl_port)
            l2_ifgroup_entry_dl.groupId = uint32_tp_value(group_id)
            l2_ifgroup_bucket_dl.groupId = l2_ifgroup_entry_dl.groupId
            l2_ifgroup_bucket_dl.bucketIndex = 0
            l2_ifgroup_bucket_dl.bucketData.l2Interface.outputPort = dl_port
            l2_ifgroup_bucket_dl.bucketData.l2Interface.popVlanTag = 1
            ofdpaGroupAdd(l2_ifgroup_entry_dl)
            ofdpaGroupBucketEntryAdd(l2_ifgroup_bucket_dl)

            # Then we set up the appropriate L3 Unicast Groups
            ul_gr_ids = {}
            for i, entry in enumerate(self.conf.upstream_group_table):
                ul_gr_id = self.add_gr_entry(entry,
                                             i + 1,
                                             l2_ifgroup_entry_ul.groupId,
                                             dl_vlan_id)
                ul_gr_ids[i] = ul_gr_id

            dl_gr_ids = {}
            for i, entry in enumerate(self.conf.downstream_group_table):
                offset = len(self.conf.upstream_group_table) + 1
                dl_gr_id = self.add_gr_entry(entry,
                                             i + offset,
                                             l2_ifgroup_entry_dl.groupId,
                                             ul_vlan_id)
                dl_gr_ids[i] = dl_gr_id

            self.set_vlan_table()

            # Add MAC address of the router into the Termination MAC Flow Table (20)
            fe = ofdpaFlowEntry_t()
            ofdpaFlowEntryInit(OFDPA_FLOW_TABLE_ID_TERMINATION_MAC, fe)
            fe.flowData.terminationMacFlowEntry.gotoTableId = OFDPA_FLOW_TABLE_ID_UNICAST_ROUTING
            mc = fe.flowData.terminationMacFlowEntry.match_criteria
            mc.inPort = ul_port
            mc.inPortMask = OFDPA_INPORT_EXACT_MASK
            mc.etherType = 0x0800
            MACAddress_set(mc.destMac, str(self.conf.sut.ul_port_mac))
            MACAddress_set(mc.destMacMask, "ff:ff:ff:ff:ff:ff")
            mc.vlanId = OFDPA_VID_PRESENT | ul_vlan_id
            mc.vlanIdMask = OFDPA_VID_PRESENT | OFDPA_VID_EXACT_MASK
            ofdpaFlowAdd(fe)

            fe = ofdpaFlowEntry_t()
            ofdpaFlowEntryInit(OFDPA_FLOW_TABLE_ID_TERMINATION_MAC, fe)
            fe.flowData.terminationMacFlowEntry.gotoTableId = OFDPA_FLOW_TABLE_ID_UNICAST_ROUTING
            mc = fe.flowData.terminationMacFlowEntry.match_criteria
            mc.inPort = dl_port
            mc.inPortMask = OFDPA_INPORT_EXACT_MASK
            mc.etherType = 0x0800
            MACAddress_set(mc.destMac, str(self.conf.sut.dl_port_mac))
            MACAddress_set(mc.destMacMask, "ff:ff:ff:ff:ff:ff")
            mc.vlanId = OFDPA_VID_PRESENT | dl_vlan_id
            mc.vlanIdMask = OFDPA_VID_PRESENT | OFDPA_VID_EXACT_MASK
            ofdpaFlowAdd(fe)

            # So here is a thing: normally in L3 Routing Table you can
            # distiguis two packets with the same IP by a VRF tag You
            # can assign a VRF by the income port and/or vlan tag But
            # it seems that in OFDPA there is no way for using VRF
            # Maybe I'am wrong but this should tell it:
            # http://lumanetworks.github.io/of-dpa/doc/html/d1/d17/structofdpaUnicastRoutingFlowMatch__s.html
            # So in the case that TIPSY wants to make two rules for
            # the same IP but with different in_port unfortunately we
            # cannot distiguis so the second rule will overwrite the
            # first one

            # Now we can add the actual IP addresses to Routing Flow
            # Table (30)
            for entry in self.conf.upstream_l3_table:
                self.add_l3_table(ul_gr_ids[entry.nhop], entry)
            for entry in self.conf.downstream_l3_table:
                self.add_l3_table(dl_gr_ids[entry.nhop], entry)


    def add_l3_table(self, group_id, entry):
        fe = ofdpaFlowEntry_t()
        ofdpaFlowEntryInit(OFDPA_FLOW_TABLE_ID_UNICAST_ROUTING, fe)
        fe.flowData.unicastRoutingFlowEntry.gotoTableId = OFDPA_FLOW_TABLE_ID_ACL_POLICY
        fe.flowData.unicastRoutingFlowEntry.groupID = group_id
        mc = fe.flowData.unicastRoutingFlowEntry.match_criteria
        mc.etherType = 0x0800
        mc.dstIp4 = self.ip_to_int(str(entry.ip))
        mc.dstIp4Mask = self.ip_prefix_to_int(entry.prefix_len)
        ofdpaFlowAdd(fe)

    def ip_to_int(self, address):
        return struct.unpack("!L", socket.inet_aton(address))[0]

    def ip_prefix_to_int(self, prefix_len):
        return 2**32 - 2**(32-prefix_len)


class Tipsy(object):

    def __init__(self, *args, **kwargs):
        super(Tipsy, self).__init__(*args, **kwargs)
        Tipsy._instance = self

        self.conf_file = conf_file
        self.ul_port = 1 # TODO
        self.dl_port = 2 # TODO

        print("conf_file: %s" % self.conf_file)

        try:
            with open(self.conf_file, 'r') as f:
                conv_fn = lambda d: ObjectView(**d)
                self.pl_conf = json.load(f, object_hook=conv_fn)
        except IOError as e:
            print('Failed to load cfg file (%s): %s' % (self.conf_file, e))
            raise(e)
        except ValueError as e:
            print('Failed to parse cfg file (%s): %s' % (self.conf_file, e))
            raise(e)
        try:
            self.pl = globals()['PL_%s' % self.pl_conf.name](self, self.pl_conf)
        except (KeyError, NameError) as e:
            print('Failed to instanciate pipeline (%s): %s' %
                  (self.pl_conf.name, e))
            raise(e)

    def initialize_datapath(self):
        subprocess.check_call(client_purge)

    def stop_datapath(self):
        subprocess.check_call(client_purge)

    def configure(self):
        self.initialize_datapath()
        self.pl.config_switch()

        try:
            requests.get(webhook_configured)
        except requests.ConnectionError:
            pass

    def stop(self):
        self.stop_datapath()


def handle_sigint(sig_num, stack_frame):
    Tipsy().stop()
signal.signal(signal.SIGINT, handle_sigint)

if __name__ == "__main__":
    Tipsy().configure()
    print('configured')
    signal.pause()

