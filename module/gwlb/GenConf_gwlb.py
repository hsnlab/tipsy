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

from gen_conf_base import GenConf as Base
from gen_conf_base import byte_seq

class GenConf (Base):
  "Cloud access-gateway & load-balancer"

  def __init__ (self, args):
    super().__init__(args)
    self.components += ['fakedrop', 'service']

  def get_prefix_tree (self, new_elements, tree=None):
    import random
    from socket import inet_ntoa, inet_aton
    from struct import pack, unpack

    if new_elements == 0:
      return tree

    if tree is None:
      tree = [('0.0.0.0', 0)]
      return self.get_prefix_tree(new_elements - 1, tree)

    ok = False
    while not ok:
      subtree = random.choice(tree)
      prefix, prefix_len = subtree
      ok = prefix_len < 24
    idx = tree.index(subtree)
    del tree[idx]

    # split subtree
    prefix_len += 1
    prefix_b = unpack('!I', inet_aton(prefix))[0]
    prefix_2 = prefix_b | (1 << (32 - prefix_len))
    prefix_2 = pack('!I', prefix_2)
    # add new subtrees
    tree += [(prefix, prefix_len), (inet_ntoa(prefix_2), prefix_len)]

    return self.get_prefix_tree(new_elements -1, tree)


  def add_service (self):
    from socket import inet_ntoa
    from struct import pack
    self.conf['service'] = []
    self.conf['gw'] = {'mac': 'aa:cc:dd:cc:ac:dc'}
    for s in range(self.args.service_num):
      backends = []
      prefixes = self.get_prefix_tree(self.args.backend_num)
      for (ip_src, prefix_len) in prefixes:
        backend = {'output': None, # send via the uplink port
                   'ip-src': ip_src,
                   'prefix-len': prefix_len}
        backends.append(backend)
      if s % 2 == 0:
        udp_dst = 319 # 319: Precision_Time_Protocol port
      else:
        udp_dst = 400 + s
      service = {'backend': backends,
                 'ip_dst': byte_seq('192.0.%d.%d', s),
                 'udp_dst': '%d' % udp_dst,
      }
      self.conf['service'].append(service)

    for idx in range(self.args.fluct_port):
      mod_port = {'action': 'mod_port',
                  'args': {
                    's_idx': idx % self.args.service_num
                  }}
      self.conf['run_time'].append(mod_port)

