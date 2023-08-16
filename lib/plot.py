#!/usr/bin/env python3

# TIPSY: Telco pIPeline benchmarking SYstem
#
# Copyright (C) 2018-2023 by its authors (See AUTHORS)
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
from pathlib import Path

try:
    import mongoquery
except ImportError:
    mongoquery = None

import find_mod
import mongo_pipeline


class ObjectView(dict):
    def __init__(self, fname=None, **kwargs):
        tmp = {k.replace('-', '_'): v for k, v in kwargs.items()}
        self.update(**tmp)

    def __getitem__(self, x):
        x = x.replace('-', '_')
        if '.' in x:
            [head, tail] = x.split('.', 1)
            return super().__getitem__(head)[tail]
        return super().__getitem__(x)

    def __getattr__(self, x):
        return self.__getitem__(x)

    def get(self, key, default):
        try:
            return self[key]
        except KeyError:
            return default

    @staticmethod
    def _to_dict(obj):
        if type(obj) == list:
            return [ ObjectView._to_dict(x) for x in obj ]
        if type(obj) == ObjectView:
            return { k: ObjectView._to_dict(v) for k, v in obj.items() }
        return obj

    @staticmethod
    def _from_dict(obj):
        if type(obj) == list:
            return [ ObjectView._from_dict(x) for x in obj ]
        if type(obj) == dict:
            d = {k: ObjectView._from_dict(v) for k, v in obj.items()}
            return ObjectView(**d)
        return obj

def json_load(file):
    with file.open('r') as f:
        data = json.load(f, object_hook=lambda x: ObjectView(**x))
    return data

def match_lt(query, obj):
    return obj < query

def match_gt(query, obj):
    return obj > query

def match_not(query, obj):
    return not match(query, obj)

def match(query, obj):
    if type(query) in [int, float, str]:
        return query == obj
    for var, sub_query in query.items():
        if var.startswith('$'):
            m = globals().get('match_%s' % var[1:])
            if m is None:
                raise NotImplementedError(var)
            if not m(sub_query, obj):
                return False
            else:
                continue
        try:
            val = obj[var]
        except KeyError:
            return False
        if not match(sub_query, val):
            return False
    return True

def eval_expr_subtract(args, data):
    if not isinstance(args, list):
        raise Exception(f"invalid args for subtract expression")
    return eval_expr(args[0], data) - eval_expr(args[1], data)

def eval_expr_divide(args, data):
    if not isinstance(args, list):
        raise Exception(f"invalid args for divide expression")
    return eval_expr(args[0], data) / eval_expr(args[1], data)

def eval_expr_setField(args, data):
    arg_field = eval_expr(args['field'], data)
    arg_input = eval_expr(args['input'], data)
    arg_value = eval_expr(args['value'], data)
    if arg_input != '$$ROOT':
        raise Exception('setField error: input must be $$ROOT')
    if arg_value == '$$REMOVE':
        del data[arg_field]
    else:
        data[arg_field] = arg_value
    return data

def eval_expr(expr, data):
    if isinstance(expr, str):
        if expr.startswith('$$'):
            return expr
        if expr.startswith('$'):
            expr = expr[1:]
        else:
            return expr
        try:
            return float(data[expr])
        except ValueError:
            return data[expr]
        except KeyError:
            return float("nan")
        raise Exception(f"cannot evaulate expression: {expr}")
    if isinstance(expr, dict):
        if len(expr) != 1:
            raise Exception(f"Unknown expression: {expr}")
        op, args = list(expr.items())[0]
        if not op.startswith('$'):
            raise Exception(f"Unknown expression: {expr}")
        e = globals().get(f'eval_expr_{op[1:]}')
        if e is None:
            raise NotImplementedError(op)
        return e(args, data)

def filter_data(conf, data):
    if not conf.filter:
        return data
    if mongoquery:
        q = mongoquery.Query(conf.filter)
        return filter(q.match, data)

    print("Can't import monoquery, using a subset of the query language")
    return [obj for obj in data if match(conf.filter, obj)]

def eval_pipeline(pipeline, data):
    try:
        ret = mongo_pipeline.eval_python_pipeline(pipeline, data)
    except Exception as e:
        print(f'python pipeline failed: {e}')
        print(f'trying to evaluate with the real mongodb backend')
        pipeline = ObjectView._to_dict(pipeline)
        data = ObjectView._to_dict(data)
        ret = mongo_pipeline.eval_mongo_pipeline(pipeline, data)
    return ObjectView._from_dict(ret)

    if not pipeline:
        return data
    import pymongo              # apt-get install python3-pymongo
    client = pymongo.MongoClient()
    db = client.tipsy
    col = db.plot
    col.delete_many({})
    col.insert_many( ObjectView._to_dict(data) )
    pipeline = ObjectView._to_dict(pipeline)

    ret = col.aggregate(pipeline)
    col.delete_many({})
    data = []
    for item in ret:
        del item["_id"]
        data.append(item)
    return ObjectView._from_dict(data)

def run_in_cwd():
    cwd = Path().cwd()
    conf = json_load(cwd / 'plot.json')
    data = []
    for res in sorted((cwd.parent.parent/'measurements').glob('*.json')):
        print(res)
        data += json_load(res)
    data = eval_pipeline(conf.aggregate, data)
    data = filter_data(conf, data)

    plt_class = find_mod.find_class('Plot', conf.type)
    plt_obj = plt_class(conf)
    plot_points = plt_obj.plot(data)
    plt_obj.write_preamble()
    with open('out.json', 'w') as f:
        json.dump(plot_points, f, indent=1)


if __name__ == "__main__":
    run_in_cwd()
