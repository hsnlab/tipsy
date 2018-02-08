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

import json
import subprocess
from pathlib import Path, PosixPath

try:
    import args_from_schema
    import wait_for_callback
except ImportError:
    from . import args_from_schema
    from . import wait_for_callback


__all__ = ["run"]

class Config(object):
    def __init__(self, *files, **kwargs):
        kw = {k.replace('-', '_'): v for k, v in kwargs.items()}
        self.__dict__.update(kw)
        for f in files:
            self.load(f)

    def __repr__(self):
        return self.__dict__.__repr__()

    def load(self, file):
        """Update dict with json encode data from `file`.
        file is a filename or Path."""

        oh = lambda x: Config(**x)
        try:
            if type(file) == PosixPath:
                with file.open('r') as f:
                    data = json.load(f, object_hook=oh)
            else:
                with open(file, 'r') as f:
                    data = json.load(f, object_hook=oh)
        except Exception as e:
            print(e)
            exit(-1)
        self.__dict__.update(**data.__dict__)


class SUT(object):
    def __init__(self, conf, **kw):
        self.conf = conf
        self.cmd_prefix = ['ssh', conf.sut.hostname]
        self.screen_name = 'TIPSY_SUT'

    def run_ssh_cmd(self, cmd, *extra_cmd):
        command = self.cmd_prefix + list(extra_cmd) + cmd
        print(' '.join(command))
        subprocess.run(command, check=True)

    def get_screen_cmd(self, cmd):
        return ['screen', '-c', '/dev/null', '-d', '-m',
                '-S', self.screen_name] + cmd

    def upload_to_remote(self, src, dst):
        "scp one file to SUT"
        dst = '%s:%s' % (self.conf.sut.hostname, dst)
        cmd = [str(c) for c in ['scp', src, dst]]
        print(' '.join(cmd))
        subprocess.run(cmd, check=True)

    def start(self, *args):
        raise NotImplementedError

    def stop(self, *args):
        cmd = ['screen', '-S', self.screen_name, '-X', 'stuff', '^C']
        self.run_ssh_cmd(cmd)

    def run_local_shell_script(self, script, path_sut):
        if Path(script).is_file():
            self.upload_to_remote(script, path_sut)
            self.run_ssh_cmd(['sh', str(path_sut)])

    def run_setup_script(self):
        self.run_local_shell_script(self.conf.sut.setup_script,
                                    Path('/tmp', 'setup.sh'))

    def run_teardown_script(self):
        self.run_local_shell_script(self.conf.sut.teardown_script,
                                    Path('/tmp', 'teardown.sh'))


class SUT_bess(SUT):
    def start(self):
        local_pipeline = Path().cwd() / 'pipeline.json'
        dst = Path('/tmp') / 'pipeline.json'
        self.upload_to_remote(local_pipeline, dst)
        cmd = [
            Path(self.conf.sut.tipsy_dir) / 'bess' / 'bess-runner.py',
            '-d', self.conf.sut.bess_dir,
            '-c', dst,
        ]

        cmd = self.get_screen_cmd([str(c) for c in cmd])
        self.run_ssh_cmd(cmd)

        cmd = Path(self.conf.sut.tipsy_dir) / 'lib' / 'wait_for_callback.py'
        self.run_ssh_cmd([str(cmd)], '-t', '-t')


class SUT_ovs(SUT):
    def __init__(self, conf):
        super().__init__(conf)

    def start(self):
        remote_ryu_dir = Path(self.conf.sut.tipsy_dir) / 'ryu'
        remote_pipeline = remote_ryu_dir / 'pipeline.json'
        local_pipeline = Path().cwd() / 'pipeline.json'
        self.upload_to_remote(local_pipeline, remote_pipeline)

        cmd = remote_ryu_dir / 'start-ryu'
        cmd = self.get_screen_cmd(['sudo', str(cmd)])
        self.run_ssh_cmd(cmd)

        cmd = Path(self.conf.sut.tipsy_dir) / 'lib' / 'wait_for_callback.py'
        self.run_ssh_cmd([str(cmd)], '-t', '-t')

class Tester(object):
    def __init__(self, conf):
        self.conf = conf

    def start(self, out_dir):
        raise NotImplementedError

    def run_script(self, script):
        if Path(script).is_file():
            cmd = [str(o) for o in ['sh', script]]
            subprocess.call(cmd)

    def run_setup_script(self):
        self.run_script(self.conf.tester.setup_script)

    def run_teardown_script(self):
        self.run_script(self.conf.tester.teardown_script)


class Tester_moongen(Tester):
    def __init__(self, conf):
        super().__init__(conf)
        self.txdev = conf.tester.uplink_port
        self.rxdev = conf.tester.downlink_port
        self.mg_cmd = conf.tester.moongen_cmd
        self.script = Path(__file__).parent.parent / 'utils' / 'mg-pcap.lua'
        self.runtime = conf.tester.test_time

    def start(self, out_dir):
        pcap = out_dir / 'traffic.pcap'
        pfix = out_dir / 'mg'
        hfile = out_dir / 'mg.histogram.csv'
        cmd = ['sudo', self.mg_cmd, self.script, self.txdev, self.rxdev, pcap,
               '-l', '-t', '-r', self.runtime, '-o', pfix, '--hfile', hfile]
        cmd = [ str(o) for o in cmd ]
        print(' '.join(cmd))
        subprocess.call(cmd)

    def stop(self):
        # TODO: curl http://sut:8080/exit
        super().stop()


def run(defaults=None):
    cwd = Path().cwd()
    conf = Config(cwd.parent.parent / '.tipsy.json')
    sut = globals()['SUT_%s' % conf.sut.type](conf)

    sut.run_setup_script()
    sut.start()

    tester = globals()['Tester_%s' % conf.tester.type](conf)
    tester.run_setup_script()
    tester.start(cwd)
    tester.run_teardown_script()

    sut.stop()
    sut.run_teardown_script()

if __name__ == "__main__":
    run()
