#!/usr/bin/env python3
import argparse
import glob
import itertools
import json
import os
import subprocess
from pathlib import Path, PosixPath

from gen.gen_conf import gen_conf
from lib import validate

def json_dump(obj, target):
    """Serialize ``obj`` as a JSON formatted text to ``target``.
target is either a filename, a PosixPath, or a file-like object.
"""
    def dump_to_file(obj, outfile):
        json.dump(obj, outfile, indent=4)
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
        tmp = {k.replace('-', '_'): v for k, v in kwargs.items()}
        self.update(*args, **tmp)

    def __getattr__(self, name):
        return self[name]

    def __setattr__(self, name, value):
        self[name] = value

    def __delattr__(self, name):
        del self[name]

    def gen_configs(self):
        assert self.benchmark
        assert self.traffic
        scale = getattr(self, '_scale_%s' % self.benchmark.scale)
        benchmarks = scale(self.benchmark.pipeline)
        traffics = self._scale_outer(self.traffic)
        self.configs = [{**l[0], **l[1]}
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


class TipsyStateManager(object):
    def __init__(self, state_json):
        self.state_json = state_json
        self.action_reqs = {'init': None,
                            'validate': None,
                            'config': ['validate'],
                            'traffic_gen': ['config'],
                            'run': ['traffic_gen'],
                            'eval': ['run'],
                            'visualize': ['eval'],
                            'make': None,
                            'clean': None}
        self._read_states()

    def get_state(self, state):
        return self.states[state]

    def set_state(self, state, value):
        self.states[state] = value
        self._write_states()

    def reset_states(self):
        actions = ['init', 'validate', 'config', 'traffic_gen',
                   'run', 'eval', 'visualize', 'make', 'clean']
        self.states = {a: False for a in actions}
        self._write_states()

    def _read_states(self):
        try:
            with open(self.state_json, 'r') as f:
                self.states = json.load(f)
        except:
            self.reset_states()

    def _write_states(self):
        json_dump(self.states, self.state_json)

    def is_state_ready(self, state):
        if self.action_reqs[state]:
            return all(self.states[s] for s in self.action_reqs[state])
        else:
            return True

    def get_reqs(self, state):
        return self.action_reqs[state]


class TipsyManager(object):
    def __init__(self, args):
        self.tipsy_dir = Path(__file__).resolve().parent
        self.args = args
        self.state_mngr = TipsyStateManager('.tipsystate')
        self.fname_pl_in = 'pipeline-in.json'
        self.fname_pl = 'pipeline.json'
        self.fname_pcap = 'trace.pcap'

    def execute_action_reqs(self, action):
        if self.state_mngr.is_state_ready(action):
            pass
        else:
            reqs = self.state_mngr.get_reqs(action)
            for req in reqs:
                getattr(self, "do_%s" % req)()

    def end_action(self, action):
        self.state_mngr.set_state(action, True)

    def _action(action_func):
        def wrapper(self, *args, **kwargs):
            action = action_func.__name__.replace('do_', '')
            self.execute_action_reqs(action)
            if not self.state_mngr.get_state(action):
                action_func(self, *args, **kwargs)
            self.end_action(action)
        return wrapper

    def _meta_action(action_func):
        def wrapper(self, *args, **kwargs):
            action_func(self, *args, **kwargs)
        return wrapper

    @_action
    def do_init(self):
        raise NotImplementedError

    def validate_json_conf(self, fname):
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

    @_action
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

    @_action
    def do_config(self):
        def conf_load(d): return TipsyConfig(**d)
        try:
            jsons = self.args.configs
            assert jsons
        except:
            jsons = [str(f) for f in Path.cwd().glob('*.json')]
        with open(jsons[0], 'r') as c:
            self.tipsy_conf = json.load(c, object_hook=conf_load)
        for conf in jsons[1:]:
            with open(conf, 'r') as conf_file:
                tmp = json.load(conf_file, object_hook=conf_load)
                self.tipsy_conf.update(tmp)
        self.tipsy_conf.gen_configs()
        try:
            os.mkdir('measurements')
        except FileExistsError:
            pass
        for i, config in enumerate(self.tipsy_conf.configs, start=1):
            out_dir = Path('measurements', '%03d' % i)
            out_dir.mkdir()
            out_conf = out_dir.joinpath(self.fname_pl)
            tmp_file = out_dir.joinpath(self.fname_pl_in)
            json_dump(config, tmp_file)
            json_dump(gen_conf(config), out_conf)

    @_action
    def do_traffic_gen(self):
        gen_pcap = self.tipsy_dir.joinpath("gen", "gen-pcap.py")
        meas_dir = Path('measurements')
        for out_dir in [f for f in meas_dir.iterdir() if f.is_dir()]:
            out_pcap = out_dir.joinpath(self.fname_pcap)
            tmp_file = out_dir.joinpath(self.fname_pl_in)
            conf_file = out_dir.joinpath(self.fname_pl)
            cmd = ("%s --json %s --conf %s --output %s"
                   % (gen_pcap, tmp_file, conf_file, out_pcap))
            subprocess.call(cmd, shell=True)

    @_action
    def do_run(self):
        raise NotImplementedError

    @_action
    def do_evaluate(self):
        raise NotImplementedError

    @_action
    def do_visualize(self):
        raise NotImplementedError

    @_meta_action
    def do_make(self):
        for cmd in ('validate', 'config', 'traffic_gen',
                    'run', 'evaluate', 'visualize'):
            getattr(self, 'do_%s' % cmd)()

    @_meta_action
    def do_clean(self):
        os.remove('.tipsystate')
        import shutil
        shutil.rmtree('measurements')
        # TODO


##################
if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='TIPSY: Telco pIPeline benchmarking SYstem')
    subparsers = parser.add_subparsers(dest='command')
    subparsers.required = True
    init = subparsers.add_parser('init',
                                 help='Init tipsy in current directory')
    init.add_argument('configs', type=str,
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
