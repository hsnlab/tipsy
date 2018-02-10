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

import time
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer

class MyHandler(BaseHTTPRequestHandler):
  def do_GET(self):
    if self.path != self.w_url:
      self.send_response(404)
      return
    self.send_response(200)
    self.send_header("Content-type", "application/json")
    self.end_headers()
    self.wfile.write(bytes("\"%s\"\n" % self.path, 'utf-8'))

    def shutdown(server):
      server.shutdown()
    Thread(target=shutdown, args=(self.httpd,)).start()

def wait_for_request(host_name, port_number, url):
  httpd = HTTPServer((host_name, port_number), MyHandler)
  httpd.RequestHandlerClass.w_url = url
  httpd.RequestHandlerClass.httpd = httpd
  interrupted = False
  try:
    httpd.serve_forever()
  except KeyboardInterrupt:
    interrupted = True
  finally:
    httpd.server_close()
  return not interrupted


if __name__ == '__main__':
  params = ('127.0.0.1', 9000, '/configured')
  print('Waiting for %s:%s%s' % params)
  if wait_for_request(*params):
    print('Got it')
  else:
    print('Failed')
    exit(-1)
