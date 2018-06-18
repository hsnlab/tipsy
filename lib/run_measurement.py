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

import csv
import datetime
import json
import subprocess
import time
from pathlib import Path, PosixPath

try:
    import args_from_schema
    import wait_for_callback
except ImportError:
    from . import args_from_schema
    from . import wait_for_callback


__all__ = ["run"]

class Config(dict):
    def __init__(self, *files, **kwargs):
        self.update(kwargs)
        for f in files:
            self.load(f)

    def __getattr__(self, name):
        return self[name.replace('_', '-')]

    def __setattr__(self, name, value):
        self[name.replace('_', '-')] = value

    def __delattr__(self, name):
        del self[name.replace('_', '-')]

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
        self.update(**data)


class SUT(object):
    def __init__(self, conf, **kw):
        self.conf = conf
        self.result = {}
        self.cmd_prefix = ['ssh', self.conf.sut.hostname]
        self.screen_name = 'tipsy-sut'

    def run_ssh_cmd(self, cmd, *extra_cmd, **kw):
        command = self.cmd_prefix + list(extra_cmd) + cmd
        print(' '.join(command))
        return subprocess.run(command, check=True, **kw)

    def run_async_ssh_cmd(self, cmd):
        command = self.cmd_prefix + ['-t'] + cmd
        command = self.get_screen_cmd(command)
        print(' '.join(command))
        subprocess.run(command, check=True)

    def get_screen_cmd(self, cmd):
        return ['screen', '-c', '/dev/null', '-d', '-m',
                '-L', '-S', self.screen_name] + cmd

    def upload_to_remote(self, src, dst):
        "scp one file to SUT"
        dst = '%s:%s' % (self.conf.sut.hostname, dst)
        cmd = [str(c) for c in ['scp', src, dst]]
        print(' '.join(cmd))
        subprocess.run(cmd, check=True)

    def upload_conf_files(self, dst_dir):
        src_dir = Path().cwd()
        dst_dir = Path(dst_dir)

        for fname in ['pipeline.json', 'benchmark.json']:
            self.upload_to_remote(src_dir / fname, dst_dir / fname)

    def start(self, *args):
        self.run_setup_script()
        self._start(*args)

    def _start(self, *args):
        raise NotImplementedError

    def stop(self, *args):
        r = self.run_ssh_cmd(['curl', '-s', '-o', '-',
                              'http://localhost:8080/tipsy/result'],
                             stdout=subprocess.PIPE, stderr=None)
        if r.stdout:
            try:
                data = json.loads(r.stdout.decode())
            except Exception as e:
                data = {'error': str(e)}
            self.result.update(**data)

        cmd = ['screen', '-S', self.screen_name, '-X', 'stuff', '^C']
        subprocess.run(cmd, check=True)
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
        self.run_ssh_cmd([str(cmd)], '-t', '-t')


class SUT_bess(SUT):
    def _start(self):
        self.result['versions'] = {}
        cmd = [str(Path(self.conf.sut.bess_dir) / 'bin' / 'bessd'), '-t']
        v = self.run_ssh_cmd(cmd, stdout=subprocess.PIPE)
        for line in v.stdout.decode('utf-8').split("\n"):
            if line.startswith(' '):
                break
            [var, val] = line.split(' ')
            self.result['versions'][var] = val
        self.result['version'] = self.result['versions'].get('bessd', 'n/a')

        remote_dir = Path('/tmp')
        self.upload_conf_files(remote_dir)
        cmd = [
            Path(self.conf.sut.tipsy_dir) / 'bess' / 'bess-runner.py',
            '-d', self.conf.sut.bess_dir,
            '-p', remote_dir / 'pipeline.json',
            '-b', remote_dir / 'benchmark.json',
        ]
        self.run_async_ssh_cmd([str(c) for c in cmd])
        self.wait_for_callback()


class SUT_vpp(SUT):
    def _start(self):
        remote_dir = Path('/tmp')
        self.upload_conf_files(remote_dir)
        cmd = [
            'python3',
            Path(self.conf.sut.tipsy_dir) / 'vpp' / 'vpp-runner.py',
            '-p', remote_dir / 'pipeline.json',
            '-b', remote_dir / 'benchmark.json'
        ]
        self.run_async_ssh_cmd([str(c) for c in cmd])
        self.wait_for_callback()

        cmd = ['sudo', 'vppctl', 'show', 'version']
        v = self.run_ssh_cmd(cmd, stdout=subprocess.PIPE)
        self.result['version'] = v.stdout.decode('utf-8').split("\n")[0]
        dpdk_cmd = ['sudo', 'vppctl', 'show', 'dpdk', 'version']
        d = self.run_ssh_cmd(dpdk_cmd, stdout=subprocess.PIPE)
        fline = d.stdout.decode('utf-8').split("\n")[0]
        dpdk_version = fline.split('DPDK')[-1].strip()
        self.result['versions'] = {}
        self.result['versions']['DPDK'] = dpdk_version


class SUT_ovs(SUT):
    def __init__(self, conf):
        super().__init__(conf)

    def _start(self):
        v = self.run_ssh_cmd(['ovs-vsctl', '--version'], stdout=subprocess.PIPE)
        first_line = v.stdout.decode('utf8').split("\n")[0]
        self.result['version'] = first_line.split(' ')[-1]

        remote_ryu_dir = Path(self.conf.sut.tipsy_dir) / 'ryu'
        self.upload_conf_files(remote_ryu_dir)

        cmd = remote_ryu_dir / 'start-ryu'
        self.run_async_ssh_cmd(['sudo', str(cmd)])
        self.wait_for_callback()


class SUT_erfs(SUT):
    def _start(self):
        remote_dir = Path(self.conf.sut.tipsy_dir) / 'erfs'
        self.upload_conf_files(remote_dir)

        cmd = remote_dir / 'start-ryu'
        self.run_async_ssh_cmd(['sudo', str(cmd)])
        self.wait_for_callback()


class SUT_ofdpa(SUT):
    def __init__(self, conf):
        super().__init__(conf)

    def _start(self):
        remote_cmd = Path(self.conf.sut.tipsy_dir) / 'ofdpa' / 'tipsy.py'
        self.upload_conf_files('/tmp')
        self.run_async_ssh_cmd(['sudo', str(remote_cmd)])
        self.wait_for_callback()


class SUT_t4p4s(SUT):
    def __init__(self, conf):
        super().__init__(conf)

    def _start(self):
        self.upload_conf_files('/tmp')
        remote_cmd = Path(self.conf.sut.tipsy_dir) / 't4p4s' / 'tipsy.py'
        self.run_async_ssh_cmd([str(remote_cmd)])
        self.wait_for_callback()


class Tester(object):
    def __init__(self, conf):
        self.conf = conf
        self.result = {}

        cwd = str(Path(__file__).parent)
        cmd = ['git', 'describe', '--dirty', '--always', '--tags']
        try:
            v = subprocess.run(cmd, stdout=subprocess.PIPE, cwd=cwd, check=True)
            self.result['tipsy-version'] = v.stdout.decode().strip()
        except Exception as e:
            self.result['tipsy-version'] = 'n/a'
            self.result['tipsy-version-error-msg'] = str(e)

    def run(self, out_dir):
        self.result['timestamp'] = int(time.time())
        self.result['iso-date'] =  datetime.datetime.now().isoformat()
        self.result['test-id'] = out_dir.name
        self.run_setup_script()
        self._run(out_dir)
        self.run_teardown_script()
        self.collect_results()

    def _run(self, out_dir):
        raise NotImplementedError

    def collect_results(self):
        raise NotImplementedError

    def run_script(self, script):
        if Path(script).is_file():
            subprocess.run([str(script)], check=True)

    def run_setup_script(self):
        self.run_script(self.conf.tester.setup_script)

    def run_teardown_script(self):
        self.run_script(self.conf.tester.teardown_script)


class Tester_moongen(Tester):
    def __init__(self, conf):
        super().__init__(conf)
        tester = conf.tester
        if conf.traffic.dir == 'uplink':
            self.txdev = tester.uplink_port
            self.rxdev = tester.downlink_port
        elif conf.traffic.dir == 'downlink':
            self.txdev = tester.downlink_port
            self.rxdev = tester.uplink_port
        else:
            raise Exception("unavailable traffic.dir: %s" % conf.traffic.dir)
        if tester.core != 1:
            self.txdev = "%s:%s" % (self.txdev, tester.core)
        self.mg_cmd = tester.moongen_cmd
        self.script = Path(__file__).parent.parent / 'utils' / 'mg-pcap.lua'
        self.runtime = tester.test_time
        self.rate_limit = 0

    def _run(self, out_dir):
        pcap = out_dir / 'traffic.pcap'
        pfix = out_dir / 'mg'
        hfile = out_dir / 'mg.histogram.csv'
        cmd = ['sudo', self.mg_cmd, self.script, self.txdev, self.rxdev, pcap,
               '-l', '-t', '-r', self.runtime, '-o', pfix, '--hfile', hfile]
        if self.rate_limit:
            cmd += ['--rate-limit', self.rate_limit]
        cmd = [ str(o) for o in cmd ]
        print(' '.join(cmd))
        subprocess.call(cmd)

    def collect_results(self):
        with open('mg.latency.csv') as f:
            reader = csv.DictReader(f)
            for row in reader:
                latency = row
        latency['unit'] = 'ns'

        throughput = {}
        with open('mg.throughput.csv') as f:
            # The last rows describe for the overall performance
            reader = csv.DictReader(f)
            for row  in reader:
                d = row.pop('Direction')
                throughput[d] = row
        self.result.update({
            'latency': latency,
            'throughput': throughput
        })


class Tester_moongen_rfc2544(Tester_moongen):
    def __init__(self, conf):
        super().__init__(conf)
        self.script = Path(__file__).parent.parent / 'utils' / 'mg-rfc2544.lua'

    def _run(self, out_dir):
        pcap = out_dir / 'traffic.pcap'
        ofile = out_dir / 'mg.rfc2544.csv'
        precision = 5
        cmd = ['sudo', self.mg_cmd, self.script, self.txdev, self.rxdev, pcap,
               '-r', self.runtime, '-p', precision, '-o', ofile]
        cmd = [ str(o) for o in cmd ]
        print(' '.join(cmd))
        subprocess.call(cmd)

    def collect_results(self):
        data = 'nan'
        with open('mg.rfc2544.csv') as f:
            reader = csv.DictReader(f)
            for row in reader:
                data = row
        self.result.update({'rfc2544': data })


class Tester_moongen_combined(Tester):
    def __init__(self, conf):
        super().__init__(conf)
        self.rfc2544 = Tester_moongen_rfc2544(conf)
        self.latency = Tester_moongen(conf)

    def _run(self, out_dir):
        self.rfc2544._run(out_dir)
        self.rfc2544.collect_results()
        limit = self.rfc2544.result['rfc2544']['limit']
        self.latency.rate_limit = limit
        self.latency._run(out_dir)

    def collect_results(self):
        self.latency.collect_results()
        self.result.update(self.rfc2544.result)
        self.result.update(self.latency.result)


class Tester_moongen_flood(Tester_moongen):
    def __init__(self, conf):
        super().__init__(conf)
        self.script = Path(__file__).parent.parent / 'utils' / 'mg-flood.lua'

    def _run(self, out_dir):
        pcap = out_dir / 'traffic.pcap'
        ofile = out_dir / 'mg.flood.csv'
        cmd = ['sudo', self.mg_cmd, self.script, self.txdev, self.rxdev,
               pcap, '-l', '-r', self.runtime, '-o', ofile]
        cmd = [ str(o) for o in cmd ]
        print(' '.join(cmd))
        subprocess.call(cmd)

    def collect_results(self):
        throughput = {}
        with open('mg.flood.csv') as f:
            reader = csv.DictReader(f)
            for row  in reader:
                d = row.pop('Direction')
                throughput[d] = row
        self.result.update({'flood': throughput})


def run(defaults=None):
    cwd = Path().cwd()
    conf = Config(cwd / 'benchmark.json')
    sut = globals()['SUT_%s' % conf.sut.type](conf)
    sut.start()

    tester_type = conf.tester.type.replace('-','_')
    tester = globals()['Tester_%s' % tester_type](conf)
    tester.run(cwd)

    sut.stop()

    result = conf
    result['out'] = {'sut': sut.result}
    result['out'].update(tester.result)
    with open('results.json', 'w') as f:
        json.dump(result, f, sort_keys=True, indent=4)


if __name__ == "__main__":
    run()
