--- Replay a pcap file to measure RFC2544 like throughput

-- TIPSY: Telco pIPeline benchmarking SYstem
--
-- Copyright (C) 2018 by its authors (See AUTHORS)
--
-- This program is free software: you can redistribute it and/or
-- modify it under the terms of the GNU General Public License as
-- published by the Free Software Foundation, either version 3 of the
-- License, or (at your option) any later version.
--
-- This program is distributed in the hope that it will be useful, but
-- WITHOUT ANY WARRANTY; without even the implied warranty of
-- MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
-- General Public License for more details.
--
-- You should have received a copy of the GNU General Public License
-- along with this program. If not, see <http://www.gnu.org/licenses/>.

local mod = {}

local device  = require "device"

function mod:setRate(dev, rate)
   local devices = device.getDevices()
   specname = 'Intel Corporation 82599ES'
   if devices[dev.id].name:sub(1, #specname) == specname then
      -- "82599 has per queue rate limit"
      -- https://blog.linuxplumbersconf.org/2012/wp-content/uploads/2012/09/2012-lpc-Hardware-Rate-Limiting-brandeburg.pdf
      for seq, que in pairs(dev.txQueues) do
         -- print('-->  ' .. seq)
         que:setRate(rate)
      end
   else
      dev:setRate(rate)
   end
end

return mod
