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

import inspect
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from find_mod import add_path
from tester_base import Tester as Base

class Tester(Base):
    def __init__(self, conf):
        super().__init__(conf)
        self.logger = logging.getLogger(__name__)
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        tester = conf.tester
        self.should_stop_daemon = False
        self.trex_host = conf.tester.trex_host
        self.client_args = conf.tester.trex_client_args
        self.cli_args = {'d': tester.test_time}
        self.cli_args.update(conf.tester.trex_cli_args)

        path = Path(tester.trex_dir) / 'trex_client' / 'stf'
        with add_path(str(path)):
            from trex_stf_lib.trex_client import CTRexClient
            self.CTRexClient = CTRexClient

        self.update_trex_cfg()

    def update_trex_cfg(self):
        def trim(addr):
            m = re.match(r'^0000:(\d{2}:\d{2}.\d)', addr)
            if m:
                return m.group(1)
            return addr
        fname = '/etc/trex_cfg.yaml'
        tmpname = '/tmp/trex_cfg.yaml'
        cfg = {
            'uplink': trim(self.conf.tester.uplink_port),
            'downlink': trim(self.conf.tester.downlink_port),
        }
        # If the the file is missing, create it
        if not os.path.exists(fname):
            try:
                cmd = 'sudo dmesg | grep -q VirtualBox'
                subprocess.run(cmd, check=True, shell=True)
                cfg['low_end'] = 'true'
            except subprocess.CalledProcessError:
                cfg['low_end'] = 'false'
            content = inspect.cleandoc("""
              - port_limit: 2
                version: 2
                interfaces: ['{uplink}', '{downlink}']
                low_end: {low_end}
                port_info   :  # set mac addr
                - dest_mac  : [0x00,0x00,0x00,0x00,0x00,0x01] # port 0
                  src_mac   : [0x00,0x00,0x00,0x00,0x00,0x02]
                - dest_mac  : [0x00,0x00,0x00,0x00,0x00,0x03] # port 1
                  src_mac   : [0x00,0x00,0x00,0x00,0x00,0x04]
            """.format(**cfg))
            with open(tmpname, 'w') as f:
                f.write("%s\n" % content)
            subprocess.run(['sudo', 'mv', tmpname, fname], check=True)
            return
        # Replace the 'interfaces' entry in the existing config.
        #
        # (It would be nicer to load the yaml file and modify it as a
        # python object, but the yaml package in trex's python-lib
        # does not work with python3.  It's also better to avoid
        # adding one additional dependency (python3-yaml) just for
        # this.)
        new = "interfaces: ['{uplink}', '{downlink}']".format(**cfg)
        exp = 's/^\(\s*\)interfaces.*$/\\1%s/' % new
        cmd = ['sudo', 'sed', '-i', '-e', exp, fname]
        subprocess.run(cmd, check=True)

    def _run(self, out_dir):
        self.start_daemon()
        self.logger.info('Connecting to %s', self.trex_host)
        self.client = self.CTRexClient(self.trex_host, **self.client_args)
        self.logger.info('Connected, running TRex for %ss', self.cli_args['d'])
        self.client.start_trex(**self.cli_args)
        self.trex_result = self.client.sample_to_run_finish()

    def collect_results(self):
        self.result['trex'] = self.trex_result.get_latest_dump()
        self.result['trex-info'] = self.client.get_trex_version()

        if self.should_stop_daemon:
            self.run_daemon_cmd('stop')

    def run_daemon_cmd(self, command, **kw):
        cwd = self.conf.tester.trex_dir
        cmd = ['sudo', './daemon_server', command]
        if command == 'start':
            r = subprocess.Popen(cmd, cwd=cwd)
            time.sleep(2)
        else:
            r = subprocess.run(cmd, check=True, cwd=cwd, **kw)
        return r

    def start_daemon(self):
        self.logger.debug('trex_host: %s', self.trex_host)
        if self.trex_host not in ['localhost', '127.0.0.1']:
            return
        r = self.run_daemon_cmd('show', stdout=subprocess.PIPE)
        output = r.stdout.decode()
        if output.startswith('TRex server daemon is NOT running'):
            self.run_daemon_cmd('start')
            self.should_stop_daemon = True
        else:
            self.logger.warn('trex daemon: %s', output)
