#!/usr/bin/env python3

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
import binascii
import itertools
import json
import re
import requests
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


class BessUpdater(object):
    def __init__(self, conf):
        self.bess = self.get_local_bess_handle()
        self.conf = conf
        self.runtime_interval = 1
        self._running = False
        self.workers_num = self.get_num_workers()

    def get_local_bess_handle(self):
        bess = BESS()
        try:
            bess.connect()
        except BESS.APIError:
            raise Exception('BESS is not running')
        return bess

    def get_num_workers(self):
        return str(self.bess.list_workers()).count("workers_status {")

    def start(self):
        self._running = True
        self._run()

    def stop(self):
        self._running = False

    def _run(self):
        actions = ('add', 'del')
        targets = ('user', 'server')
        tasks = ['_'.join(e) for e in itertools.product(actions, targets)]
        table_actions = ('mod_table', 'mod_l3_table', 'mod_group_table')
        while self._running:
            for task in self.conf.run_time:
                if task.action == 'handover':
                    teid = task.args.user_teid
                    shift = task.args.bst_shift
                    user = [u for u in self.conf.users if u.teid == teid][0]
                    new_bst = self._calc_new_bst_id(user.tun_end, shift)
                    self.handover(user, new_bst)
                elif task.action in table_actions:
                    self.mod_table(task.action, task.cmd,
                                   task.table, task.entry)
                elif task.action in tasks:
                    getattr(self, task.action)(task.args)
            time.sleep(self.runtime_interval)

    def _calc_new_bst_id(self, cur_bst_id, bst_shift):
        return (cur_bst_id + bst_shift) % len(self.conf.bsts)

    def _config_add(self, entry, conf_container, key):
        con = getattr(self.conf, conf_container)
        if not any(e for e in con if getattr(e, key) == getattr(entry, key)):
            con.append(entry)

    def _config_del(self, entry, conf_container, key):
        con = getattr(self.conf, conf_container)
        for i, e in enumerate(con):
            if getattr(e, key) == getattr(entry, key):
                con.pop(i)
                break

    def _config_add_user(self, user):
        self._config_add(user, 'users', 'teid')

    def _config_del_user(self, user):
        self._config_del(user, 'users', 'teid')

    def _config_add_server(self, server):
        self._config_add(server, 'srvs', 'ip')

    def _config_del_server(self, server):
        self._config_del(server, 'srvs', 'ip')

    def _config_handover(self, user, new_bst):
        for i, usr in enumerate(self.conf.users):
            if usr.teid == user.teid:
                usr.tun_end = new_bst
                self.conf.users[i] = usr

    def _config_mod_table(self, action, cmd, table, entry):
        key = 'mac'
        if 'l3' in action:
            key = 'ip'
        if 'group' in action:
            key = 'dmac'
        try:
            params = (table, action.replace('mod_', ''))
            tab = getattr(self.conf, '%s_%s' % params)
        except:
            raise ValueError
        if cmd == 'add':
            if not any(e for e in tab if
                       getattr(e, key) == getattr(entry, key)):
                tab.append(entry)
        elif cmd == 'del':
            for i, e in enumerate(tab):
                if getattr(e, key) == getattr(entry, key):
                    tab.pop(i)
                    break
        else:
            raise ValueError

    def add_user(self, user):
        raise NotImplementedError

    def del_user(self, user):
        raise NotImplementedError

    def add_server(self, server):
        raise NotImplementedError

    def del_server(self, server):
        raise NotImplementedError

    def handover(self, user, new_bst):
        raise NotImplementedError

    def mod_table(self, action, cmd, table, entry):
        raise NotImplementedError


class BessUpdaterDummy(BessUpdater):
    def _run(self):
        while self._running:
            time.sleep(self.runtime_interval)


class BessUpdaterL2Fwd(BessUpdater):
    def __init__(self, conf):
        super(BessUpdaterL2Fwd, self).__init__(conf)

    def mod_table(self, action, cmd, table, entry):
        self._config_mod_table(action, cmd, table, entry)
        for wid in range(self.conf.core):
            wid2 = self.conf.core + wid
            name = 'mac_table_%s_%d' % (table[0], wid)
            buf = 'out_buf_%s_%d' % (table[0], wid)
            if table[0] == 'u':
                ogate = entry.out_port or len(self.conf.upstream_table) + 1
            else:
                ogate = entry.out_port or len(self.conf.downstream_table) + 1
            try:
                self.bess.pause_worker(wid)
                self.bess.pause_worker(wid2)
                if cmd == 'add':
                    self.bess.run_module_command(name,
                                                 'add', 'ExactMatchCommandAddArg',
                                                 {'fields':
                                                  [{'value_bin':
                                                    mac_from_str(entry.mac)}],
                                                  'gate': ogate})
                    self.bess.connect_modules(name, buf,
                                              ogate, 0)
                elif cmd == 'del':
                    self.bess.run_module_command(name,
                                                 'delete', 'ExactMatchCommandDeleteArg',
                                                 {'fields':
                                                  [{'value_bin':
                                                    mac_from_str(entry.mac)}]})
                    self.bess.disconnect_modules(name, ogate + 1)
            except BESS.Error:
                raise
            finally:
                self.bess.resume_worker(wid)
                self.bess.resume_worker(wid2)


class BessUpdaterL3Fwd(BessUpdater):
    def __init__(self, conf):
        super(BessUpdaterL3Fwd, self).__init__(conf)

    def mod_table(self, action, cmd, table, entry):
        self._config_mod_table(action, cmd, table, entry)
        if 'l3' in action:
            self.mod_l3_table(cmd, table, entry)
        elif 'group' in action:
            self.mod_group_table(cmd, table, entry)

    def mod_l3_table(self, cmd, table, entry):
        for wid in range(self.conf.core):
            try:
                wid2 = self.conf.core + wid
                self.bess.pause_worker(wid)
                self.bess.pause_worker(wid2)
                name = 'l3fib_%s_%d' % (table[0], wid)
                ip = re.sub(r'\.[^.]+$', '.0', entry.ip)
                gat = entry.nhop + 1
                if cmd == 'add':
                    self.bess.run_module_command(name,
                                                 'add', 'IPLookupCommandAddArg',
                                                 {'prefix': ip,
                                                  'prefix_len': entry.prefix_len,
                                                  'gate': gat})
                elif cmd == 'del':
                    self.bess.run_module_command(name,
                                                 'delete', 'IPLookupCommandDeleteArg',
                                                 {'prefix': ip,
                                                  'prefix_len': entry.prefix_len})
            except BESS.Error:
                raise
            finally:
                self.bess.resume_worker(wid)
                self.bess.resume_worker(wid2)

    def mod_group_table(self, cmd, table, entry):
        for wid in range(self.conf.core):
            wid2 = self.conf.core + wid
            try:
                self.bess.pause_worker(wid)
                self.bess.pause_worker(wid2)
                l3fib = 'l3fib_%s_%d' % (table[0], wid)
                ip_chk = 'ip_chk_%s_%d' % (table[0], wid)
                tab = getattr(self.conf, '%s_group_table' % table)
                i_tab = enumerate(tab, start=1)
                uid = next((i for (i, v) in i_tab if v.dmac == entry.dmac),
                           len(tab))
                if cmd == 'add':
                    name = 'up_dmac_x_%d_%s_%d' % (uid, table[0], wid)
                    self.bess.create_module('Update', name,
                                            {'fields':
                                             [{'offset': 0, 'size': 6,
                                               'value': mac_int_from_str(entry.dmac)}]})
                    self.bess.connect_modules(l3fib, name, uid, 0)
                    self.bess.connect_modules(name, ip_chk, 0, 0)
                elif cmd == 'del':
                    name = 'up_dmac_x_%d_%s_%d' % (uid + 1, table[0], wid)
                    self.bess.destroy_module(name)
            except BESS.Error:
                raise
            finally:
                self.bess.resume_worker(wid)
                self.bess.resume_worker(wid2)


class BessUpdaterMgw(BessUpdater):
    def __init__(self, conf):
        super(BessUpdaterMgw, self).__init__(conf)

    def add_user(self, user):
        self._config_add_user(user)
        for wid in range(self.workers_num):
            try:
                self.bess.pause_worker(wid)
                self.bess.run_module_command('ue_selector_%d' % wid,
                                             'add', 'ExactMatchCommandAddArg',
                                             {'fields': [{'value_bin': aton(user.ip)}],
                                              'gate': user.teid})
                self.bess.resume_worker(wid)
                md_name = 'setmd_dl_%d_%d' % (user.teid, wid)
                tun_ip_dst = self.conf.bsts[user.tun_end].ip
                self.bess.create_module('SetMetadata', md_name,
                                        {'attrs': [{'name': 'tun_id', 'size': 4,
                                                    'value_int': user.teid},
                                                   {'name': 'tun_ip_src', 'size': 4,
                                                    'value_bin': aton(self.conf.gw.ip)},
                                                   {'name': 'tun_ip_dst', 'size': 4,
                                                    'value_bin': aton(tun_ip_dst)}]})
                self.bess.connect_modules('ue_selector_%d' % wid, md_name,
                                          user.teid, 0)
                self.bess.connect_modules(md_name, 'vxlan_encap_%d' % wid,
                                          0, 0)
                q_name = 'rl_%d_%d' % (wid, user.teid)
                self.bess.create_module('Queue', q_name)
                self.bess.connect_modules(q_name, 'prel3_buf_%d' % wid, 0, 0)
                self.bess.connect_modules('teid_split_%d' % wid, q_name,
                                          user.teid, 0)
                t_name = 't_%d_%d' % (wid, user.teid)
                try:
                    self.bess.add_tc(t_name, policy='rate_limit',
                                     resource='bit', limit={'bit': user.rate_limit},
                                     wid=wid)
                except BESS.Error:
                    pass
                self.bess.attach_task(q_name, parent=t_name)
            except BESS.Error:
                raise
            finally:
                self.bess.resume_worker(wid)

    def del_user(self, user):
        self._config_del_user(user)
        for wid in range(self.workers_num):
            try:
                self.bess.pause_worker(wid)
                self.bess.run_module_command('ue_selector_%d' % wid,
                                             'delete', 'ExactMatchCommandDeleteArg',
                                             {'fields': [{'value_bin': aton(user.ip)}]})
                md_name = 'setmd_dl_%d_%d' % (user.teid, wid)
                self.bess.destroy_module(md_name)
                q_name = 'rl_%d_%d' % (wid, user.teid)
                self.bess.destroy_module(q_name)
            except BESS.Error:
                raise
            finally:
                self.bess.resume_worker(wid)

    def handover(self, user, new_bst):
        self._config_handover(user, new_bst)
        for wid in range(self.workers_num):
            try:
                tun_ip_dst = self.conf.bsts[user.tun_end].ip
                md_name = 'setmd_dl_%d_%d' % (user.teid, wid)
                self.bess.destroy_module(md_name)
                self.bess.create_module('SetMetadata', md_name,
                                        {'attrs': [{'name': 'tun_id', 'size': 4,
                                                    'value_int': user.teid},
                                                   {'name': 'tun_ip_src', 'size': 4,
                                                    'value_bin': aton(self.conf.gw.ip)},
                                                   {'name': 'tun_ip_dst', 'size': 4,
                                                    'value_bin': aton(tun_ip_dst)}]})
                self.bess.connect_modules('ue_selector_%d' % wid, md_name,
                                          user.teid, 0)
                self.bess.connect_modules(md_name, 'vxlan_encap_%d' % wid,
                                          0, 0)
            except BESS.Error:
                pass

    def __get_id_from_ip(self, ip):
        return sum([int(x[0]) * x[1] for x in zip(ip.split('.')[1:3], (255, 1))])

    def add_server(self, server):
        self._config_add_server(server)
        for wid in range(self.workers_num):
            try:
                ip = re.sub(r'\.[^.]+$', '.0', server.ip)
                id = self.__get_id_from_ip(ip)
                ogate = len(self.conf.srvs) + id
                self.bess.pause_worker(wid)
                self.bess.run_module_command('ip_lookup_%d' % wid,
                                             'add', 'IPLookupCommandAddArg',
                                             {'prefix': ip, 'prefix_len': 24,
                                              'gate': ogate})
                self.bess.resume_worker(wid)
                md_name = 'setmd_srv%d_%d' % (ogate, wid)
                self.bess.create_module('SetMetadata', md_name,
                                        {'attrs': [{'name': 'nhop', 'size': 4,
                                                    'value_int': server.nhop}]})
                self.bess.connect_modules('ip_lookup_%d' % wid, md_name,
                                          ogate, 0)
            except BESS.Error:
                raise
            finally:
                self.bess.resume_worker(wid)

    def del_server(self, server):
        self._config_del_server(server)
        for wid in range(self.workers_num):
            try:
                ip = re.sub(r'\.[^.]+$', '.0', server.ip)
                id = self.__get_id_from_ip(ip)
                ogate = len(self.conf.srvs) + id + 1
                self.bess.pause_worker(wid)
                self.bess.run_module_command('ip_lookup_%d' % wid,
                                             'delete', 'IPLookupCommandDeleteArg',
                                             {'prefix': ip, 'prefix_len': 24})
                self.bess.disconnect_modules('ip_lookup_%d' % wid, ogate)
                md_name = 'setmd_srv%d_%d' % (ogate, wid)
                self.bess.destroy_module(md_name)
            except BESS.Error:
                raise
            finally:
                self.bess.resume_worker(wid)


class BessUpdaterBng(BessUpdaterMgw):
    def __init__(self, conf):
        super(BessUpdaterBng, self).__init__(conf)
        self.conf.bsts = self.conf.cpe

    def handover(self, user, new_bst):
        pass


class ObjectView(object):
    def __init__(self, **kwargs):
        tmp = {k.replace('-', '_'): v for k, v in kwargs.items()}
        self.__dict__.update(**tmp)

    def __repr__(self):
        return self.__dict__.__repr__()


def aton(ip):
    return socket.inet_aton(ip)


def mac_from_str(s):
    return binascii.unhexlify(s.replace(':', ''))


def mac_int_from_str(s):
    return int("0x%s" % ''.join(s.split(':')), 16)


def signal_handler(signum, frame):
    updater.stop()


def call_cmd(cmd):
    print(' '.join(cmd))
    return subprocess.call(cmd)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bessdir', '-d', type=str,
                        help='BESS root directory',
                        default='~/bess', required=True)
    parser.add_argument('--pl-conf', '-p', type=argparse.FileType('r'),
                        help='Pipeline config JSON file',
                        default='./pipeline.json')
    parser.add_argument('--bm-conf', '-b', type=argparse.FileType('r'),
                        help='Benchmark config JSON file',
                        default='./pipeline.json')
    args = parser.parse_args()

    bessctl = str(Path(args.bessdir, 'bessctl', 'bessctl'))

    try:
        sys.path.insert(1, args.bessdir)
        from pybess.bess import BESS
    except ImportError:
        print(('Cannot import the API module (pybess) from %s' % args.bessdir))
        raise

    try:
        def conv_fn(d): return ObjectView(**d)
        pl_config = json.load(args.pl_conf, object_hook=conv_fn)
        bm_config = json.load(args.bm_conf, object_hook=conv_fn)
    except:
        raise

    pipeline_bess = str(
        Path(__file__).parent.joinpath('%s.bess' % pl_config.name))
    bess_start_cmd = [bessctl,
                      'daemon', 'start', '--',
                      'run', 'file',
                      pipeline_bess,
                      'pl_config=\"%s\",bm_config=\"%s\"' %
                      (args.pl_conf.name, args.bm_conf.name)]
    ret_val = call_cmd(bess_start_cmd)
    try:
        url = 'http://localhost:9000/configured'
        requests.get(url)
    except requests.ConnectionError:
        pass
    if not ret_val:

        try:
            uclass = getattr(sys.modules[__name__],
                             'BessUpdater%s' % pl_config.name.title())
            updater = uclass(pl_config)
        except:
            updater = BessUpdaterDummy(pl_config)

        signal.signal(signal.SIGINT, signal_handler)

        updater.start()

    bess_stop_cmd = [bessctl, 'daemon', 'stop']
    call_cmd(bess_stop_cmd)
