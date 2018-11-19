--- Replay a pcap as fast as possible.

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


local mg      = require "moongen"
local device  = require "device"
local memory  = require "memory"
local stats   = require "stats"
local log     = require "log"
local pcap    = require "pcap"
local timer   = require "timer"

function configure(parser)
   parser:description("Replay a PCAP file and measure throughput.")
   parser:argument("txDev", "txport")
      :default(0)
      :convert(tonumber)
   parser:argument("rxDev", "rxport")
      :default(1)
      :convert(tonumber)
   parser:argument("file", "pcap file")
      :args(1)
   parser:option("-r --runtime", "running time in seconds.")
      :default(0)
      :convert(tonumber)
   parser:flag("-l --loop", "repeat pcap file")
   parser:option("-o --ofile", "file to use for saving the results")
      :default(nil)
   local args = parser:parse()
   return args
end

function master(args)
   local txDev, rxDev
   if args.txDev ~= args.rxDev then
     txDev = device.config({port = args.txDev, txQueues = 1, rxQueues = 1})
     rxDev = device.config({port = args.rxDev, rxQueues = 1, txQueues = 1})
   else
      txDev = device.config({port = args.txDev, txQueues = 1, rxQueues = 1})
      rxDev = txDev
   end
   device.waitForLinks()
   mg.startTask("replay_pcap", txDev:getTxQueue(0), args.file, args.loop,
                rxDev:getRxQueue(0))
   if args.ofile then
      stats.startStatsTask{txDevices = {txDev}, rxDevices = {rxDev},
                           format="csv", file=args.ofile}
   else
      stats.startStatsTask{txDevices = {txDev}, rxDevices = {rxDev},
                           format="plain"}
   end
   if args.runtime > 0 then
      mg.setRuntime(args.runtime)
   end
   mg.waitForTasks()
end

function replay_pcap(queue, file, loop, rxQueue)
   local rxMempool = memory.createMemPool()
   local rxBufs = rxMempool:bufArray()

   local mempool = memory:createMemPool(4096)
   local bufs = mempool:bufArray()
   local pcapFile = pcap:newReader(file)
   local n = pcapFile:read(bufs)
   pcapFile:reset()
   while mg.running() do
      local n = pcapFile:read(bufs)
      if n == 0 then
	 if loop then
	    pcapFile:reset()
	 else
	    break
	 end
      end
      queue:sendN(bufs, n)

      rxQueue:tryRecvIdle(rxBufs, 5)
      rxBufs:freeAll()
   end
end
