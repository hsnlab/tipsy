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
--
-- This file incorporates work (i.e., portions of MoonGen example
-- scripts) covered by the following copyright and permission notice:
--
-- Copyright (c) 2014 Paul Emmerich
--
-- Permission is hereby granted, free of charge, to any person
-- obtaining a copy of this software and associated documentation
-- files (the "Software"), to deal in the Software without
-- restriction, including without limitation the rights to use, copy,
-- modify, merge, publish, distribute, sublicense, and/or sell copies
-- of the Software, and to permit persons to whom the Software is
-- furnished to do so, subject to the following conditions:
--
-- The above copyright notice and this permission notice shall be
-- included in all copies or substantial portions of the Software.
--
-- THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
-- EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
-- MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
-- NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
-- BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
-- ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
-- CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
-- SOFTWARE.

require "colors"

local mg      = require "moongen"
local device  = require "device"
local memory  = require "memory"
local stats   = require "stats"
local log     = require "log"
local pcap    = require "pcap"
local timer   = require "timer"

function configure(parser)
   parser:description("Measure RFC2544 like throughput with replaying a PCAP"
                      .. " file for a certain duration.")
   parser:argument("txDev","txport[:numcores]"):default(0)
   parser:argument("rxDev", "rxport"):default(1):convert(tonumber)
   parser:argument("file", "pcap file"):args(1)
   parser:option("-r --runtime", "length of one measurement."):default(0):convert(tonumber)
   parser:option("-p --precision", "precision [Mbit/s]\n"
                    .. "default: 1% of the link rate."):default(0):convert(tonumber)
   parser:option("-o --ofile", "file to write the result into."):default(nil)
   local args = parser:parse()
   return args
end

local binarySearch = {}
binarySearch.__index = binarySearch

function binarySearch:create(lower, upper)
    local self = setmetatable({}, binarySearch)
    self.lowerLimit = lower
    self.upperLimit = upper
    return self
end
setmetatable(binarySearch, { __call = binarySearch.create })

function binarySearch:init(lower, upper)
    self.lowerLimit = lower
    self.upperLimit = upper
end

function binarySearch:next(curr, top, threshold)
    if top then
        if curr == self.upperLimit then
            return curr, true
        else
            self.lowerLimit = curr
        end
    else
        if curr == lowerLimit then
            return curr, true
        else
            self.upperLimit = curr
        end
    end
    local nextVal = math.ceil((self.lowerLimit + self.upperLimit) / 2)
    if math.abs(nextVal - curr) < threshold then
        return curr, true
    end
    return nextVal, false
end

function master(args)
   if args.runtime == 0 then
      log:error("Runtime cannot be 0, use the -r option")
      return
   end

   local txport, cores
   if args.txDev:find(":") then
      txport, cores = tonumberall(args.txDev:match("(%d+):(%d+)"))
   else
      txport, cores = tonumber(args.txDev), 1
   end
   local txDev, rxDev
   if txport ~= args.rxDev then
     txDev = device.config({port = txport, txQueues = cores, rxQueues = 2})
     rxDev = device.config({port = args.rxDev, rxQueues = cores, txQueues = 2})
   else
      txDev = device.config({port = txport,
                             txQueues = cores, rxQueues = cores})
      rxDev = txDev
   end
   device.waitForLinks()
   -- stats.startStatsTask{txDevices = {txDev}, rxDevices = {rxDev}, format="plain"}

   a = {txDev=txDev, rxDev=rxDev, cores=cores, rate=rate,
        file=args.file, runtime=args.runtime}

   local linkRate = txDev:getLinkStatus().speed
   local rateThreshold = args.precision==0 and linkRate*0.01 or args.precision
   local binSearch = binarySearch(0, linkRate)
   local finished = false
   local validRun, tx, rx
   a.rate = linkRate
   log:info("Precision: %d [Mbit/s]", rateThreshold)

   while not finished do
      log:info('Sending pcap with rate of %16s%s',
               green(tostring(a.rate)),
               white(' [Mbit/s] for %ds', a.runtime))
      tx, rx = measure_with_rate(a)
      validRun = (tx < rx + rateThreshold) and (tx >= a.rate*0.9)
      log:info('  result: tx:%5.0f rx:%5.0f [Mbit/s] %s', tx, rx, validRun)
      a.rate, finished = binSearch:next(a.rate, validRun, rateThreshold)
   end
   log:info('Result: %s%s', yellow(tostring(a.rate)), white(' [Mbit/s]'))
   if args.ofile then
      file = io.open(args.ofile, "w")
      file:write("limit,tx,rx,unit\n")
      file:write(tostring(a.rate), ",",
                tostring(tx), ",",
                tostring(rx), ",Mbit/s\n")
      file:close()
   end
end

function measure_with_rate(...)
   a = ...
   local rxCtr = stats:newDevRxCounter(a.rxDev, "nil")
   local txCtr = stats:newDevTxCounter(a.txDev, "nil")
   txCtr:update()
   rxCtr:update()
   for i = 1, a.cores do
      mg.startTask("replay_pcap", a.txDev:getTxQueue(i-1), a.file, true)
   end
   a.txDev:setRate(a.rate)
   mg.setRuntime(a.runtime)

   mg.waitForTasks()
   txCtr:update()
   rxCtr:update()
   txCtr:finalize(0)
   rxCtr:finalize(0)
   local tx_stats = txCtr:getStats()
   local rx_stats = rxCtr:getStats()

   log:info('  result: tx:%2.2f rx:%2.2f [Mpps]', txCtr.mpps[1], rxCtr.mpps[1])
   return txCtr.wireMbit[1], rxCtr.wireMbit[1]
end

function replay_small_pcap(queue, bufs, n)
   log:info('The pcap file is small (#packet: %d)', n)
   local len = n
   while len < 30 do
      for i = 0, n-1 do
         len = len + 1
         bufs.array[len-1] = bufs.array[i]
      end
   end
   while mg.running() do
      queue:sendN(bufs, len)
   end
end

function replay_pcap(queue, file, loop)
   local mempool = memory:createMemPool(4096)
   local bufs = mempool:bufArray()
   local pcapFile = pcap:newReader(file)
   local n = pcapFile:read(bufs)
   pcapFile:reset()
   if n < 30 and loop then
      return replay_small_pcap(queue, bufs, n)
   end
   while mg.running() do
      local n = pcapFile:read(bufs)
      if n <= 0 then
	 if loop then
	    pcapFile:reset()
	 else
	    break
	 end
      end
      queue:sendN(bufs, n)
   end
end
