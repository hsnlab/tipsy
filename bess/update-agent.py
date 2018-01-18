#!/usr/bin/env python2
import os
import sys
import time
import json
import signal
import socket
import argparse
import itertools
import subprocess


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
        if self.conf.run_time:
            self._running = True
            self._run()

    def stop(self):
        self._running = False

    def _run(self):
        actions = ('add', 'del')
        targets = ('user', 'server')
        tasks = ['_'.join(e) for e in itertools.product(actions, targets)]
        while self._running:
            for task in self.conf.run_time:
                if task.action == 'handover':
                    teid = task.args.user_teid
                    shift = task.args.bst_shift
                    user = [u for u in self.conf.users if u.teid == teid][0]
                    new_bst = self._calc_new_bst_id(user.tun_end, shift)
                    self.handover(user, new_bst)
                elif task.action in tasks:
                    getattr(self, task.action)(task.args)
            time.sleep(self.runtime_interval)

    def _calc_new_bst_id(self, cur_bst_id, bst_shift):
        return (cur_bst_id + bst_shift) % len(self.conf.bsts)

    def _config_add_user(self, user):
        if not any(u for u in self.conf.users if u.teid == user.teid):
            self.conf.users.append(user)

    def _config_del_user(self, user):
        for i, u in enumerate(self.conf.users):
            if u.teid == user.teid:
                self.conf.users.pop(i)
                break

    def _config_add_server(self, server):
        if not any(s for s in self.conf.srvs if s.ip == server.ip):
            self.conf.srvs.append(server)

    def _config_del_server(self, server):
        for i, s in enumerate(self.conf.srvs):
            if s.ip == server.ip:
                self.conf.srvs.pop(i)
                break

    def _config_handover(self, user, new_bst):
        for i, usr in enumerate(self.conf.users):
            if usr.teid == user.teid:
                usr.tun_end = new_bst
                self.conf.users[i] = usr

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
                self.bess.add_tc(t_name, policy='rate_limit',
                                 resource='bit', limit={'bit': user.rate_limit},
                                 wid=wid)
                self.bess.attach_task(q_name, parent=t_name)
            except BESS.Error:
                self.bess.resume_worker(wid)

    def del_user(self, user):
        self._config_del_user(user)
        for wid in range(self.workers_num):
            try:
                self.bess.pause_worker(wid)
                self.bess.run_module_command('ue_selector_%d' % wid,
                                             'delete', 'ExactMatchCommandDeleteArg',
                                             {'fields': [{'value_bin': aton(user.ip)}]})
            except BESS.Error:
                pass
            finally:
                self.bess.resume_worker(wid)

    def handover(self, user, new_bst):
        self._config_handover(user, new_bst)
        for wid in range(self.workers_num):
            try:
                md_name = 'setmd_dl_%d_%d' % (user.teid, wid)
                tun_ip_dst = self.conf.bsts[user.tun_end].ip
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
                pass
            finally:
                self.bess.resume_worker(wid)

    def del_server(self, server):
        self._config_del_server(server)
        for wid in range(self.workers_num):
            try:
                ip = re.sub(r'\.[^.]+$', '.0', server.ip)
                id = self.__get_id_from_ip(ip)
                ogate = len(self.conf.srvs) + id
                self.bess.pause_worker(wid)
                self.bess.run_module_command('ip_lookup_%d' % wid,
                                             'delete', 'IPLookupCommandDeleteArg',
                                             {'prefix': ip, 'prefix_len': 24})
                self.bess.disconnect_modules('ip_lookup_%d' % wid, ogate)
            except BESS.Error:
                pass
            finally:
                self.bess.resume_worker(wid)


class BessUpdaterVmgw(BessUpdater):
    def __init__(self, conf):
        super(BessUpdaterVmgw, self).__init__(conf)

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
                self.bess.add_tc(t_name, policy='rate_limit',
                                 resource='bit', limit={'bit': user.rate_limit},
                                 share=1, parent='traffic_mgmt_%d' % wid)
                self.bess.attach_task(q_name, parent=t_name)
            except BESS.Error:
                self.bess.resume_worker(wid)

    def del_user(self, user):
        self._config_del_user(user)
        for wid in range(self.workers_num):
            try:
                self.bess.pause_worker(wid)
                self.bess.run_module_command('ue_selector_%d' % wid,
                                             'delete', 'ExactMatchCommandDeleteArg',
                                             {'fields': [{'value_bin': aton(user.ip)}]})
            except BESS.Error:
                pass
            finally:
                self.bess.resume_worker(wid)

    def handover(self, user, new_bst):
        self._config_handover(user, new_bst)
        for wid in range(self.workers_num):
            try:
                md_name = 'setmd_dl_%d_%d' % (user.teid, wid)
                tun_ip_dst = self.conf.bsts[user.tun_end].ip
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
                pass
            finally:
                self.bess.resume_worker(wid)

    def del_server(self, server):
        self._config_del_server(server)
        for wid in range(self.workers_num):
            try:
                ip = re.sub(r'\.[^.]+$', '.0', server.ip)
                id = self.__get_id_from_ip(ip)
                ogate = len(self.conf.srvs) + id
                self.bess.pause_worker(wid)
                self.bess.run_module_command('ip_lookup_%d' % wid,
                                             'delete', 'IPLookupCommandDeleteArg',
                                             {'prefix': ip, 'prefix_len': 24})
                self.bess.disconnect_modules('ip_lookup_%d' % wid, ogate)
            except BESS.Error:
                pass
            finally:
                self.bess.resume_worker(wid)


class BessUpdaterBng(BessUpdater):
    def __init__(self, conf):
        super(BessUpdaterBng, self).__init__(conf)
        raise NotImplementedError


class ObjectView(object):
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __repr__(self):
        return self.__dict__.__repr__()


def aton(ip):
    return socket.inet_aton(ip)


def signal_handler(signum, frame):
    updater.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bessdir', '-d', type=str,
                        help='BESS root directory',
                        default='~/bess', required=True)
    parser.add_argument('--conf', '-c', type=argparse.FileType('r'),
                        help='Pipeline config JSON file',
                        default='./mgw_conf.json')
    args = parser.parse_args()

    bessctl = os.path.join(args.bessdir, 'bessctl', 'bessctl')

    try:
        sys.path.insert(1, args.bessdir)
        from pybess.bess import *
    except ImportError:
        print('Cannot import the API module (pybess) from %s' % args.bessdir)
        raise

    try:
        def conv_fn(d): return ObjectView(**d)
        config = json.load(args.conf, object_hook=conv_fn)
    except:
        print('Error loading config from %s' % args.conf)
        raise

    bess_start_cmd = "%s daemon start -- run file ./%s.bess \"config='%s'\"" % (
        bessctl, config.name, args.conf.name)
    print(bess_start_cmd)
    subprocess.call(bess_start_cmd, shell=True)

    try:
        uclass = getattr(sys.modules[__name__],
                         'BessUpdater%s' % config.name.title())
        updater = uclass(config)
    except:
        raise

    signal.signal(signal.SIGINT, signal_handler)

    updater.start()

    bess_stop_cmd = "%s daemon stop" % bessctl
    print(bess_stop_cmd)
    subprocess.call(bess_stop_cmd, shell=True)
