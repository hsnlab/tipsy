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
import json
import logging
import subprocess
import time
from pathlib import Path, PosixPath

logging.basicConfig(level=logging.DEBUG)

class SUT(object):
    def __init__(self, conf, **kw):
        self.conf = conf
        self.result = {}
        self.cmd_prefix = ['ssh', self.conf.sut.hostname]
        self.screen_name = 'tipsy-sut'
        name = inspect.getmodule(self.__class__).__name__
        self.logger = logging.getLogger(name)

    def run_ssh_cmd(self, cmd, *extra_cmd, **kw0):
        kw = {'check': True}
        kw.update(**kw0)

        command = self.cmd_prefix + list(extra_cmd) + cmd
        self.logger.info(' '.join(command))
        return subprocess.run(command, **kw)

    def run_async_ssh_cmd(self, cmd):
        command = self.cmd_prefix + ['-t'] + cmd
        command = self.get_screen_cmd(command)
        self.logger.info(' '.join(command))
        subprocess.run(command, check=True)

    def get_screen_cmd(self, cmd):
        return ['screen', '-c', '/dev/null', '-d', '-m',
                '-L', '-S', self.screen_name] + cmd

    def upload_to_remote(self, src, dst):
        "scp one file to SUT"
        dst = '%s:%s' % (self.conf.sut.hostname, dst)
        cmd = [str(c) for c in ['scp', src, dst]]
        self.logger.info(' '.join(cmd))
        subprocess.run(cmd, check=True)

    def upload_conf_files(self, dst_dir):
        src_dir = Path().cwd()
        dst_dir = Path(dst_dir)

        for fname in ['pipeline.json', 'benchmark.json']:
            self.upload_to_remote(src_dir / fname, dst_dir / fname)

    def start(self, *args):
        self.run_setup_script()
        self._query_version()
        self._start(*args)

    def _query_version(self):
        pass

    def _start(self, *args):
        raise NotImplementedError

    def stop(self, *args):
        r = self.run_ssh_cmd(['curl', '-s', '-o', '-',
                              'http://localhost:8080/tipsy/result'],
                             stdout=subprocess.PIPE, stderr=None, check=False)
        if r.stdout:
            try:
                data = json.loads(r.stdout.decode())
            except Exception as e:
                data = {'error': str(e)}
            self.result.update(**data)

        cmd = ['screen', '-S', self.screen_name, '-X', 'stuff', '^C']
        subprocess.run(cmd, check=True)

        cmd = ['screen', '-ls', self.screen_name]
        while True:
            try:
                time.sleep(2)
                subprocess.run(cmd, check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.logger.warn('Waiting for screen to stop')
            except subprocess.CalledProcessError:
                break

        self.run_teardown_script()

    def run_script(self, script):
        if Path(script).is_file():
            subprocess.run([str(script)], check=True)

    def run_setup_script(self):
        self.run_script(self.conf.sut.setup_script)

    def run_teardown_script(self):
        self.run_script(self.conf.sut.teardown_script)

    def wait_for_callback(self):
        cmd = Path(self.conf.sut.tipsy_dir) / 'lib' / 'wait_for_callback.py'
        try:
            self.run_ssh_cmd([str(cmd)], '-t', '-t')
        except subprocess.CalledProcessError as e:
            screen_log = str(Path().cwd() / 'screenlog.0')
            self.logger.critical('%s', e)
            self.logger.critical('For details, run: cat %s', screen_log)
            exit(-1)
