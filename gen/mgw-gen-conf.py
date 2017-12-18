#!/usr/bin/env python
import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument('--json', '-j', type=argparse.FileType('r'),
                    help='Input config file, '
                    'command line arguments override settings')
parser.add_argument('--output', '-o', type=argparse.FileType('w'),
                    help='Output file',
                    default='/dev/stdout')
parser.add_argument('--user', '-u', type=int,
                    help='Number of users (max: 65536)',
                    default=1)
parser.add_argument('--server', '-s', type=int,
                    help='Number of servers (max: 65536)',
                    default=1)
parser.add_argument('--bst', '-b', type=int,
                    help='Number of base stations (max: 65536)',
                    default=1)
parser.add_argument('--rate-limit', '-r', type=int,
                    help='Rate limiter [bit/s]',
                    default=10000)
parser.add_argument('--nhop', '-n', type=int,
                    help='Number of next-hops towards the servers',
                    default=2)
parser.add_argument('--fw-rules', '-f', type=int,
                    help='Number of firewall rules in virtual MGW',
                    default=1)
parser.add_argument('--gw-ip', type=str,
                    help='Gateway IP address',
                    default='200.0.0.1')
parser.add_argument('--gw-mac', type=str,
                    help='Gateway MAC address',
                    default='aa:22:bb:44:cc:66')
parser.add_argument('--downlink-default-gw-ip', type=str,
                    help='Default gateway IP address, downlink direction',
                    default='200.0.0.222')
parser.add_argument('--downlink-default-gw-mac', type=str,
                    help='Default gateway MAC address, downlink direction',
                    default='aa:22:bb:44:cc:67')
parser.add_argument('--virtual', dest='virtual',
                    help='Generate config for virtual MGW '
                    '(multiple apps are not supported at the moment)',
                    action='store_true')
parser.add_argument('--no-virtual', dest='virtual',
                    help='Do not generate config for virtual MGW '
                    '(default)',
                    action='store_false')
parser.add_argument('--napps', type=int,
                    help='Number of apps in the virtual MGW setting '
                    '(default: 1)',
                    default=1)
parser.add_argument('--fluct-user', type=int,
                    help='Number of fluctuating users per second '
                    '(default: 0)',
                    default=0)
parser.add_argument('--handover', type=int,
                    help='Number of handovers per second '
                    '(default: 0)',
                    default=0)
parser.add_argument('--fluct-server', type=int,
                    help='Number of fluctuating servers per second '
                    '(default: 0)',
                    default=0)
parser.add_argument('--fakedrop', dest='fakedrop',
                    help='If enabled, packages to drop are '
                    'forwarded to outport instead of drop. '
                    '(default)',
                    action='store_true')
parser.add_argument('--no-fakedrop', dest='fakedrop',
                    help='If disabled, packages to drop are '
                    'really droped.',
                    action='store_false')
parser.set_defaults(virtual=False)
parser.set_defaults(fakedrop=True)
args = parser.parse_args()
if args.json:
    new_defaults = json.load(args.json)
    parser.set_defaults(**new_defaults)
    args = parser.parse_args()

if args.napps != 1:
    raise NotImplementedError('Currently, one app is supported.')

bsts = []
for b in range(args.bst):
    bsts.append({
        'id': b,
        'mac': 'aa:cc:dd:cc:%02x:%02x' % (int(b / 254), (b % 254) + 1),
        'ip': '1.1.%d.%d' % (int(b / 254), (b % 254) + 1),
        'port': None,
    })

srvs = []
for s in range(args.server):
    srvs.append({
        'ip': '2.%d.%d.2' % (int(s / 254), (s % 254) + 1),
        'nhop': s % args.nhop
    })

nhops = []
for n in range(args.nhop):
    nhops.append({
        'dmac': 'aa:bb:bb:aa:%02x:%02x' % (int(n / 254), (n % 254) + 1),
        'smac': 'ee:dd:dd:aa:%02x:%02x' % (int(n / 254), (n % 254) + 1),
        'port': None,
    })

users = []
for u in range(args.user):
    users.append({
        'ip': '3.3.%d.%d' % (int(u / 254), (u % 254) + 1),
        'bst': u % len(bsts),
        'teid': u + 1,
        'rate_limit': args.rate_limit,
    })


ul_fw_rules = []
dl_fw_rules = []
dcgw = {}
if args.virtual:
    dcgw['vni'] = 666
    dcgw['ip'] = '211.0.0.1'
    dcgw['mac'] = 'ac:dc:AC:DC:ac:dc'
    for i in range(args.fw_rules):
        ul_fw_rules.append({
            'src_ip': '25.%d.1.1' % i,
            'dst_ip': '26.%d.2.2' % (200 - i),
            'src_port': 1000 + i,
            'dst_port': 1500 - i,
        })
        dl_fw_rules.append({
            'src_ip': '27.%d.1.1' % i,
            'dst_ip': '28.%d.2.2' % (200 - i),
            'src_port': 1000 + i,
            'dst_port': 1500 - i,
        })

# Generate extra users to fluctuate
extra_users = []
for u in range(args.fluct_user):
    extra_users.append({
        'ip': '4.4.%d.%d' % (int(u / 254), (u % 254) + 1),
        'bst': u % len(bsts),
        'teid': u + len(users) + 1,
        'rate_limit': args.rate_limit,
    })

# Generate extra servers to fluctuate
extra_servers = []
for s in range(args.fluct_server):
    extra_servers.append({
        'ip': '5.%d.%d.2' % (int(s / 254), (s % 254) + 1),
        'nhop': s % args.nhop
    })

# Commands that should be executed in every second
run_time = []
for i in range(args.handover):
    # new_bst_id = (old_bst_id + bst_shift) % len(bsts)
    run_time.append({'action': 'handover',
                     'args': {
                         'user_teid': users[i % args.user]['teid'],
                         'bst_shift': i + 1,
                     }})
fl_u_add = []
fl_u_del = []
for i in range(args.fluct_user):
    user = extra_users[i]
    fl_u_add = fl_u_add + [{'action': 'add_user', 'args': user}]
    fl_u_del = [{'action': 'del_user', 'args': user}] + fl_u_del
run_time = fl_u_add + run_time + fl_u_del

fl_s_add = []
fl_s_del = []
for i in range(args.fluct_server):
    server = extra_servers[i]
    fl_s_add = fl_s_add + [{'action': 'add_server', 'args': server}]
    fl_s_del = [{'action': 'del_server', 'args': server}] + fl_s_del
run_time = fl_s_add + run_time + fl_s_del

# Dump config to file
conf = {'bsts': bsts,
        'srvs': srvs,
        'nhops': nhops,
        'users': users,
        'gw': {
            'ip': args.gw_ip,
            'mac': args.gw_mac,
            'default_gw' : {'ip': args.downlink_default_gw_ip,
                            'mac': args.downlink_default_gw_mac,
            },
        },
        'ul_fw_rules': ul_fw_rules,
        'dl_fw_rules': dl_fw_rules,
        'virtual_mgw': args.virtual,
        'fakedrop': args.fakedrop,
        'dcgw': dcgw,
        'run_time': run_time,
        }

json.dump(conf, args.output, sort_keys=True, indent=4)
args.output.write("\n")
