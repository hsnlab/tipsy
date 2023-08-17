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

# This module implements some of the mongodb API.  Good performance is
# not a goal.  However, the output should not change whether the
# --server CLI argument is specified or not.

import copy
import json
import bson

class MongoEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, bson.objectid.ObjectId):
            return f'ObjectId({obj._ObjectId__id.hex()})'
        return json.JSONEncoder.default(self, obj)

def del_field(fields, data):
    if fields == []:
        return data
    data[field[0]] = del_field(fields[1:], data[field[0]])
    return data

def get_field(fields, data, env):
    if len(fields) == 0:
        return data
    field = fields[0]
    if field.startswith('$'):
        return get_field(fields[1:], env[field[1:]], env)
    else:
        field_val = data.get(field, None)
        if field_val is None:
            return None
        else:
            return get_field(fields[1:], field_val, env)


def set_field(fields, data, value, env):
    if len(fields) == 0:
        data = value
    elif fields[0].startswith('$$'):
        data = env.get(fields[0], {})
        data = set_field(fields[1:], data, value, env)
    else:
        data[fields[0]] = set_field(fields[1:],
                                    data.get(fields[0], {}),
                                    value,
                                    env)
    return data

def get_new_env(old_env, item):
    env = {'ROOT': item}
    env.update(old_env or {})
    env.update({'CURRENT': item})
    return env

# ---------------------------------------------------------------------------

def match_gt(query, obj):
    return obj > query

def match_in(query, obj):
    for sub_query in query:
        if match(sub_query, obj):
            return True
    return False

def match_lt(query, obj):
    return obj < query

def match_not(query, obj):
    return not match(query, obj)

def match(query, obj):
    if query is None:
        return obj is None
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
            val = None
        if not match(sub_query, val):
            return False
    return True


# ---------------------------------------------------------------------------

def eval_expr_addFields(args, data, env):
    res = []
    for item in data:
        sub_env = get_new_env(env, item)
        for field, field_expr in args.items():
            set_field(field.split('.'),
                      item,
                      eval_expr(field_expr, item, sub_env),
                      sub_env)
        res.append(item)
    return res

def eval_expr_arrayToObject(args, data, env):
    array = eval_expr(args, data, env)
    obj = {}
    for item in array:
        if isinstance(item, dict):
            obj[item['k']] = item['v']
        elif isinstance(item, list):
            obj[item[0]] = obj[item[1]]
        else:
            raise Exception(f'arrayToObject: unknown item format: {item}')
    return obj

def eval_expr_avg(args, data, env):
    values = []
    for item in data:
        value = eval_expr(args, item, env)
        if isinstance(value, (int, float)):
            values.append(value)
    if values:
        return sum(values)/len(values)
    else:
        float("nan")

def eval_expr_concat(args, data, env):
    return ''.join([eval_expr(item, data, env) for item in args])

def eval_expr_eq(args, data, env):
    a = eval_expr(args[0], data, env)
    b = eval_expr(args[1], data, env)
    return a == b

def eval_expr_filter(args, data, env):
    arg_cond = args['cond']
    arg_input = eval_expr(args['input'], data, env)

    res = []
    for item in arg_input:
        arg_as = eval_expr(args['as'], item, env)
        sub_env = copy.deepcopy(env)
        sub_env.update({arg_as: item, 'CURRENT': item})
        match = eval_expr(arg_cond, item, sub_env)
        if match:
            res.append(item)
    return res

def eval_expr_first(args, data, env):
    if data is None:
        return None
    if isinstance(data, list):
        return eval_expr(args, data[0], env)
    else:
        return eval_expr(args, data, env)[0]

def eval_expr_divide(args, data, env):
    if not isinstance(args, list):
        raise Exception(f"invalid args for divide expression")
    return (eval_expr(args[0], data, env) /
            eval_expr(args[1], data, env))

def eval_expr_group(args, data, env):
    if '_id' not in args.keys():
        raise Exception('$group error: field _id not found')
    if not isinstance(data, list):
        raise Exception('$group error: it must operate on an list')
    groups = {}
    for item in data:
        _id = eval_expr(args['_id'], item, env)
        id_hash = json.dumps(_id, sort_keys=True)
        groups[id_hash] = groups.get(id_hash, []) + [item]
    res = []
    for k, v in groups.items():
        item = {}
        for field, expr in args.items():
            if field == '_id':
                item['_id'] = json.loads(k)
            else:
                item[field] = eval_expr(expr, v, env)
        res.append(item)
    return res

def eval_expr_ifNull(args, data, env):
    res = eval_expr(args[0], data, env)
    if res is None:
        res = eval_expr(args[1], data, env)
    return res

def eval_expr_map(args, data, env):
    arg_in = args['in']
    arg_input = eval_expr(args['input'], data, env)

    res = []
    for item in arg_input:
        arg_as = eval_expr(args['as'], item, env)
        sub_env = copy.deepcopy(env)
        sub_env.update({arg_as: item, 'CURRENT': item})
        new_value = eval_expr(arg_in, item, sub_env)
        res.append(new_value)
    return res

def eval_expr_match(args, data, env):
    res = []
    for item in data:
        if match(args, item):
            res.append(item)
    return res

def eval_expr_multiply(args, data, env):
    if not isinstance(args, list):
        raise Exception(f"invalid args for subtract expression")

    res = 1
    for item in args:
        a = eval_expr(item, data, env)
        if a is None:
            return None
        try:
            res = res * a
        except TypeError:
            return float("nan")
    return res

def eval_expr_objectToArray(args, data, env):
    obj = eval_expr(args, data, env)
    return [{"k": k, "v": v} for k, v in obj.items()]

def eval_expr_project(args, data, env):
    result = []
    for item in data:
        sub_env = get_new_env(env, item)
        new_item = {}
        spec = {'_id': 1}
        spec.update(args)
        for field, field_expr in spec.items():
            if ((isinstance(field_expr, int) and field_expr != 0)
                or field_expr == True):
                new_value = get_field(field.split('.'), item, sub_env)
            elif ((isinstance(field_expr, int) and field_expr == 0)
                  or field_expr == False):
                new_value = del_field(field.split('.'), item)
            else:
                new_value = eval_expr(field_expr, item, sub_env)
            set_field(field.split('.'), new_item, new_value, sub_env)
        result.append(new_item)
    return result

def eval_expr_push(args, data, env):
    ret = []
    for item in data:
        ret.append(eval_expr(args, item, env))
    return ret

def eval_expr_reduce(args, data, env):
    arg_input = eval_expr(args['input'], data, env)
    value = eval_expr(args['initialValue'], data, env)
    arg_in = args['in']
    for this in arg_input:
        env = copy.deepcopy(env)
        env.update({'this': this, 'value': value})
        value = eval_expr(arg_in, data, env)
    return value

def eval_expr_replaceWith(args, data, env):
    return [eval_expr(args, item, env) for item in data]

def eval_expr_setField(args, data, env):
    arg_field = eval_expr(args['field'], data, env)
    arg_input = eval_expr(args['input'], data, env)
    arg_value = eval_expr(args['value'], arg_input, env)

    if arg_input == '$$ROOT':
        arg_input = data
    if arg_value == '$$REMOVE':
        data = del_field(arg_field.split('.'), arg_input)
    else:
        data = set_field(arg_field.split('.'), arg_input, arg_value, env)
    return data

def eval_expr_sort(args, data, env):
    res = data
    for field, order in sorted(args.items(), reverse=True):
        res = sorted(res,
                     key=lambda x: get_field(field.split('.'), x, env),
                     reverse=(order == -1))
    return res

def eval_expr_stdDevPop(args, data, env):
    mean = eval_expr_avg(args, data, env)

    values = []
    for item in data:
        value = eval_expr(args, item, env)
        if isinstance(value, (int, float)):
            values.append((value - mean)**2)
    if values:
        return (sum(values)/len(values))**0.5
    else:
        None

def eval_expr_subtract(args, data, env):
    if not isinstance(args, list):
        raise Exception(f"invalid args for subtract expression")

    a = eval_expr(args[0], data, env)
    b = eval_expr(args[1], data, env)
    if a is None or b is None:
        return None
    try:
        return a - b
    except TypeError:
        return float("nan")

def eval_expr_unwind(args, data, env):
    res = []
    if not args.startswith('$'):
        raise Exception(f'unwind: path should be prefixed with a $: {args}')
    path = args[1:]
    for item in data:
        array = get_field(path.split('.'), item, env)
        for value in array:
            new_item = copy.deepcopy(item)
            new_item = set_field(path.split('.'), new_item, value, env)
            res.append(new_item)
    return res


def eval_expr(expr, data, env=None):
    if env is None:
        env = {}
    if isinstance(expr, str):
        if expr.startswith('$'):
            return get_field(expr[1:].split('.'), data, env)
        else:
            return expr
        try:
            return float(data[expr])
        except ValueError:
            return data[expr]
        except KeyError:
            return float("nan")
        raise Exception(f"cannot evaulate expression: {expr}")
    if isinstance(expr, int) or isinstance(expr, float):
        return expr
    if isinstance(expr, dict):
        if len(expr) != 1:
            res = {}
            for field, sub_expr in expr.items():
                res[field] = eval_expr(sub_expr, data, env)
            return res
        op, args = list(expr.items())[0]
        if not op.startswith('$'):
            return {op: eval_expr(args, data, env)}
        e = globals().get(f'eval_expr_{op[1:]}')
        if e is None:
            raise NotImplementedError(op)
        return e(args, data, env)
    if isinstance(expr, list):
        # ???
        return [eval_expr(item, data, env) for item in expr]

def eval_python_pipeline(expr, data):
    ret = data
    for sub_expr in expr:
        ret = eval_expr(sub_expr, ret)
    return ret

def eval_mongo_pipeline(pipeline, data):
    import pymongo

    client = pymongo.MongoClient()
    db = client.tipsy
    col = db.plot
    col.delete_many({})
    col.insert_many(data)
    ret = col.aggregate(pipeline)
    col.delete_many({})
    return [item for item in ret]

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='eval mongo expression')
    parser.add_argument('-c', '--compact', action='store_true',
                        help='compact instead of pretty-printed output')
    parser.add_argument('-s', '--server', action='store_true',
                        help='connect to a real mongodb server')
    parser.add_argument('expr', type=argparse.FileType('r'), nargs=1)
    parser.add_argument('obj', type=argparse.FileType('r'), nargs=1)
    args = parser.parse_args()
    expr = json.load(args.expr[0])
    obj  = json.load(args.obj[0])
    if args.server:
        ret = eval_mongo_pipeline(expr, obj)
    else:
        ret  = eval_python_pipeline(expr, obj)
    if args.compact:
        print(json.dumps(ret, sort_keys=True, cls=MongoEncoder))
    else:
        print(json.dumps(ret, sort_keys=True, indent=2, cls=MongoEncoder))
