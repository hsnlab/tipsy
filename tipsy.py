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
import glob
import inspect
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path, PosixPath

from lib.gen_conf import gen_conf
from lib.gen_pcap import gen_pcap
from lib import validate


def json_dump(obj, target):
    """Serialize ``obj`` as a JSON formatted text to ``target``.
target is either a filename, a PosixPath, or a file-like object.
"""
    def dump_to_file(obj, outfile):
        json.dump(obj, outfile, indent=4, sort_keys=True)
        outfile.write("\n")

    if type(target) == PosixPath:
        with target.open('w') as outfile:
            dump_to_file(obj, outfile)
    elif type(target) == str:
        with open(target, 'w') as outfile:
            dump_to_file(obj, outfile)
    else:
        dump_to_file(obj, target)


class TipsyConfig(dict):
    def __init__(self, *args, **kwargs):
        self.update(*args, **kwargs)

    def __getattr__(self, name):
        return self[name.replace('-', '_')]

    def __setattr__(self, name, value):
        self[name.replace('_', '-')] = value

    def __delattr__(self, name):
        del self[name.replace('-', '_')]

    def gen_configs(self):
        assert self.benchmark
        assert self.traffic
        scale = getattr(self, '_scale_%s' % self.benchmark.scale)
        benchmarks = scale(self.benchmark.pipeline)
        traffics = self._scale_outer(self.traffic)
        self.configs = [{'pipeline': l[0], 'traffic': l[1]}
                        for l in itertools.product(benchmarks, traffics)]

    def _scale_none(self, conf_dict):
        return [{k: v[0] if type(v) == list else v
                 for k, v in conf_dict.items()}]

    def _scale_outer(self, conf_dict):
        tmp = {k: v if type(v) == list else [v] for k, v in conf_dict.items()}
        return list((dict(zip(tmp, x))
                     for x in itertools.product(*tmp.values())))

    def _scale_joint(self, conf_dict):
        min_len = min([len(v)
                       for k, v in conf_dict.items() if type(v) == list])
        ret_list = []
        for i in range(min_len):
            ret_list.append({k: v[i] if type(v) == list else v
                             for k, v in conf_dict.items()})
        return ret_list


class SUTConnector(object):
    def __init__(self, sut_conf):
        self.sut_conf = sut_conf
        self.ssh_access = self.sut_conf.mgmt.split(':')[1]
        self.ssh_cmd_pre = "ssh %s " % self.ssh_access
        self.screen_name = 'TIPSY_SUT'

    def run_ssh_cmd(self, cmd):
        subprocess.call("%s '%s'" % (self.ssh_cmd_pre, cmd), shell=True)

    def get_screen_cmd(self, cmd):
        params = (self.screen_name, cmd)
        return "screen -c /dev/null -d -m -S %s bash -c \"%s\"" % params

    def start_sut(self, *args):
        raise NotImplementedError

    def stop_sut(self, *args):
        cmd = "screen -S %s -X stuff ^C" % self.screen_name
        self.run_ssh_cmd(cmd)


class BessConnector(SUTConnector):
    def start_sut(self, json):
        # TODO: copy json, etc
        path = '/dev/null'  # TODO
        params = (path, self.sut_conf.bess_dir, json)
        bu = "cd %s; ./bess-runner.py -d %s -c %s" % params
        cmd = self.get_screen_cmd(bu)
        self.run_ssh_cmd(cmd)


class OvsConnector(SUTConnector):
    def start_sut(self):
        cd = "cd %s" % ryu_dir
        ryu = "ryu-manager --config-dir ."
        cmd = self.get_screen_cmd("%s; %s" % (cd, ryu))
        self.run_ssh_cmd(cmd)


class TesterRunner(object):
    def run(self, out_dir):
        raise NotImplementedError


class MoongenRunner(TesterRunner):
    def __init__(self, tipsy_conf):
        self.txdev = tipsy_conf.environment.tester.uplink_port
        self.rxdev = tipsy_conf.environment.tester.downlink_port
        self.mg_dir = tipsy_conf.environment.tester.moongen_dir
        self.script = tipsy_dir.joinpath('utils', 'mg-pcap.lua').resolve()
        self.runtime = tipsy_conf.sut.test_time

    def run(self, out_dir):
        pcap = Path(dir).joinpath('traffic.pcap').resolve()
        pfix = str(Path(dir).joinpath('mg').resolve())
        hfile = Path(dir).joinpath('%s.histogram.csv' % pfix).resolve()
        params = (self.mg_dir, self.script, self.txdev, self.rxdev,
                  pcap, self.runtime, pfix, hfile)
        cmd = ('sudo %s/build/MoonGen %s %s %s %s -l -t -r %s -o %s --hfile %s'
               % params)
        subprocess.call(cmd, shell=True)


class TipsyManager(object):
    def __init__(self, args):
        self.tipsy_dir = Path(__file__).resolve().parent
        self.args = args
        self.fname_pl_in = 'pipeline-in.json'
        self.fname_pl = 'pipeline.json'
        self.fname_pcap = 'traffic.pcap'
        self.fname_conf = '.tipsy.json'

    def do_init(self):
        fname = 'main.json'
        data = {'benchmark': {'pipeline': {'name': self.args.pipeline}}}
        validate.validate_data(data, schema_name='main')
        json_dump(data, fname)
        print(inspect.cleandoc("""
          The sample config file ({fname}) has been created.
          Edit it, then run: "{prg} config"
        """.format(fname=fname, prg=sys.argv[0])))

    def validate_json_conf(self, fname, data=None):
        if data is None:
            with open(fname) as f:
                data = json.load(f)
        try:
            validate.validate_data(data, schema_name='pipeline')
        except Exception as e:
            print('Validation failed for: %s' % fname)
            # We should tell the exact command to run
            print('For details run something like: '
                  'validate.py -s schema/pipeline-mgw.json %s' % fname)
            exit(-1)

    def do_validate(self, cli_args=None):
        # TODO: set cli_args in the caller

        join = os.path.join
        if cli_args:
            for fname in cli_args:
                self.validate_json_conf(fname)
        elif os.path.exists(self.fname_pl_in):
            self.validate_json_conf(self.fname_pl_in)
        elif os.path.exists('measurements'):
            p = join('measurements', '[0-9][0-9][0-9]', self.fname_pl_in)
            for fname in glob.glob(p):
                self.validate_json_conf(fname)
        else:
            p = join('[0-9][0-9][0-9]', self.fname_pl_in)
            for fname in glob.glob(p):
                self.validate_json_conf(fname)

    def validate_main(self):
        # We cannot fully validate a configuration until we generate
        # the exact config with the "scale" property.  But some errors
        # can be catched if allow a property to be either its original
        # type or an array of the type.
        try:
            validate.validate_data(self.tipsy_conf,
                                   schema_name='main',
                                   extension='property_array')
        except Exception as e:
            print("Validation failed for: %s\n%s" % (self.fname_conf, e))
            exit(-1)

    def init_tipsyconfig(self):
        def conf_load(d): return TipsyConfig(**d)
        try:
            jsons = self.args.configs
        except:
            if Path(self.fname_conf).exists():
                jsons = [self.fname_conf]
            else:
                jsons = [str(f) for f in Path.cwd().glob('*.json')]
        self.tipsy_conf = conf_load({})
        for conf in jsons:
            with open(conf, 'r') as cf:
                tmp = json.load(cf, object_hook=conf_load)
                self.tipsy_conf.update(tmp)
        self.validate_main()
        tmp_file = self.tipsy_dir.joinpath(self.fname_conf)
        json_dump(self.tipsy_conf, tmp_file)

    def create_file_from_template(self, src, dst, replacements):
        content = src.read_text()
        for old, new in replacements.items():
            content = content.replace('@%s@' % old, new)
        dst.write_text(content)

    def write_per_dir_makefile(self, out_dir):
        src = Path(__file__).parent / 'lib' / 'per-dir-makefile.in'
        dst = out_dir / 'Makefile'
        replacements = {'tipsy': str(Path(__file__).resolve())}
        self.create_file_from_template(src, dst, replacements)

    def write_main_makefile(self, out_dir):
        src = Path(__file__).parent / 'lib' / 'main-makefile.in'
        dst = out_dir / 'Makefile'
        replacements = {'tipsy': str(Path(__file__).resolve())}
        self.create_file_from_template(src, dst, replacements)

    def json_validate_and_dump(self, data, outfile, schema_name):
        # Treat the 'pipeline' schema specially, because the errors of
        # the general check is not very helpful.
        if schema_name == 'pipeline':
            schema_name = 'pipeline-%s' % data.get('name', '')
        try:
            validate.validate_data(data, schema_name=schema_name)
        except Exception as e:
            print("Failed validating %s:\n%s" % (outfile, e))
            exit(-1)
        json_dump(data, outfile)

    def do_config(self):
        self.init_tipsyconfig()
        self.tipsy_conf.gen_configs()
        try:
            os.mkdir('measurements')
        except FileExistsError:
            pass
        self.write_main_makefile(Path.cwd())
        save = self.json_validate_and_dump
        for i, config in enumerate(self.tipsy_conf.configs, start=1):
            out_dir = Path('measurements', '%03d' % i)
            out_dir.mkdir()
            save(config['pipeline'], out_dir / self.fname_pl_in, 'pipeline')
            save(config['traffic'], out_dir / 'traffic.json', 'traffic')
            json_dump(gen_conf(config['pipeline']), out_dir / self.fname_pl)
            self.write_per_dir_makefile(out_dir)

    def do_traffic_gen(self):
        meas_dir = Path('measurements')
        for out_dir in [f for f in meas_dir.iterdir() if f.is_dir()]:
            # NB: We cannot use gen_pcap as a lib, because
            # python3-scapy does not support VXLAN headers
            use_pcap_lib = False
            if use_pcap_lib:
                args = {
                    'output': out_dir.joinpath(self.fname_pcap),
                    'conf': out_dir.joinpath(self.fname_pl),
                    'json': out_dir.joinpath(self.fname_pl_in),
                }
                gen_pcap(args)
            else:
                gen_pcap = self.tipsy_dir.joinpath("lib", "gen_pcap.py")
                out_pcap = out_dir.joinpath(self.fname_pcap)
                tmp_file = out_dir.joinpath(self.fname_pl_in)
                conf_file = out_dir.joinpath(self.fname_pl)
                cmd = [gen_pcap, '--json', tmp_file,
                       '--conf', conf_file, '--output', out_pcap]
                cmd = [str(x) for x in cmd]
                subprocess.call(cmd)


    def run_tester(self, dir):
        tester = getattr(sys.modules[__name__],
                         "%Runner" % self.tipsy_conf.tester.type.title())
        test_runner = tester(self.tipsy_conf)
        test_runner.run(dir)

    def do_run(self):
        self.init_tipsyconfig()
        meas_dir = Path('measurements')
        if type(self.tipsy_conf.sut.type) is not list:
            self.tipsy_conf.sut.type = [self.tipsy_conf.sut.type]
        for sut in self.tipsy_conf.sut.type:
            con_init = getattr(sys.modules[__name__],
                               "%sConnector" % sut.title())
            sut_con = con_init(self.tipsy_conf.environment.sut)
            for out_dir in [f for f in meas_dir.iterdir() if f.is_dir()]:
                pl_json = str(out_dir.joinpath(self.fname_pl).resolve())
                sut_con.start_sut(pl_json)
                self.run_tester(out_dir)
                sut_con.stop_sut()

    def do_evaluate(self):
        raise NotImplementedError

    def do_visualize(self):
        raise NotImplementedError

    def do_make(self):
        for cmd in ('validate', 'config', 'traffic_gen',
                    'run', 'evaluate', 'visualize'):
            getattr(self, 'do_%s' % cmd)()

    def do_clean(self):
        os.remove(self.fname_conf)
        import shutil
        shutil.rmtree('measurements')
        # TODO


def list_pipelines():
    schema_dir = Path(__file__).resolve().parent / 'schema'
    pl = []
    for fname in schema_dir.glob('pipeline-*.json'):
        pl.append(fname.stem.replace('pipeline-', ''))
    return sorted(pl)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='TIPSY: Telco pIPeline benchmarking SYstem')
    subparsers = parser.add_subparsers(dest='command')
    subparsers.required = True
    init = subparsers.add_parser('init',
        help='Init tipsy in current directory with a sample configuration')
    init.formatter_class = argparse.ArgumentDefaultsHelpFormatter
    init.add_argument('pipeline', type=str, nargs='?',
                      choices=list_pipelines(),
                      help='Pipeline name', default='mgw')
    vali = subparsers.add_parser('validate', help='Validate configurations')
    vali.add_argument('configs', type=argparse.FileType('r'),
                      help='Config JSON files', nargs='*',
                      default=None)
    config = subparsers.add_parser('config', help='Configure TIPSY')
    config.add_argument('configs', type=str, nargs='*',
                        help='Config JSON files')
    tgen = subparsers.add_parser('traffic-gen', help='Generate traffic')
    run = subparsers.add_parser('run', help='Run benchmarks')
    eval = subparsers.add_parser('evaluate', help='Evaluate benchmark results')
    visu = subparsers.add_parser('visualize', help='Visualize results')
    make = subparsers.add_parser('make', help='Do everything')
    clean = subparsers.add_parser('clean', help='Clean up pcaps, logs, etc.')

    try:
        import argcomplete
        argcomplete.autocomplete(parser)
    except ImportError:
        pass
    args = parser.parse_args()

    tipsy = TipsyManager(args)
    action = getattr(tipsy, 'do_%s' % args.command.replace('-', '_'))
    action()
