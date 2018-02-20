--- Replay a pcap file

require "colors"

local mg      = require "moongen"
local device  = require "device"
local memory  = require "memory"
local stats   = require "stats"
local log     = require "log"
local pcap    = require "pcap"
local limiter = require "software-ratecontrol"
local timer   = require "timer"

function configure(parser)
   parser:description("Test precision of txDev:setRate().")
   parser:argument("txDev", "txport[:numcores]"):default(0)
   parser:argument("file", "pcap file"):args(1)
   parser:option("-r --runtime", "length of one measurement."):default(0):convert(tonumber)
   parser:option("-o --ofile", "file to write the result into."):default(nil)
   local args = parser:parse()
   return args
end

function print_res(file, rate, txWireMbit, txMbit)
   log:info('  result: tx:%5.0f [Mbit/s]', txMbit)
   if not file then
      return
   end
   file:write(tostring(rate), ",", txWireMbit, ",", txMbit, "\n")
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
   local txDev

   txDev = device.config({port = txport, txQueues = cores, rxQueues = 1})
   device.waitForLinks()

   a = {txDev=txDev, cores=cores, rate=rate, file=args.file, runtime=args.runtime}

   local file
   if args.ofile then
      file = io.open(args.ofile, "w")
   end

   local linkRate = txDev:getLinkStatus().speed
   local rateThreshold = linkRate * 0.01
   local finished = false
   local validRun, tx

   a.rate = linkRate

   while a.rate > 0 do
      log:info('Sending pcap with rate of %16s%s',
               green(tostring(a.rate)),
               white(' [Mbit/s] for %ds', a.runtime))
      txWireMbit, txMbit = measure_with_rate(a)
      print_res(file, a.rate, txWireMbit, txMbit)

      a.rate = a.rate - linkRate * 0.01
   end
   if file then
      file:close()
   end
end

function measure_with_rate(...)
   a = ...
   local txCtr = stats:newDevTxCounter(a.txDev, "nil")
   txCtr:update()
   for i = 1, a.cores do
      mg.startTask("replay_pcap", a.txDev:getTxQueue(i-1), a.file, true)
   end
   a.txDev:setRate(a.rate)
   mg.setRuntime(a.runtime)

   mg.waitForTasks()
   txCtr:update()
   txCtr:finalize(0)
   local tx_stats = txCtr:getStats()
   log:info('  result: tx:%2.2f [Mpps]', txCtr.mpps[1])
   return txCtr.wireMbit[1], txCtr.mbit[1]
end

function replay_pcap(queue, file, loop)
   local mempool = memory:createMemPool(4096)
   local bufs = mempool:bufArray()
   local pcapFile = pcap:newReader(file)
   local prev = 0
   local linkSpeed = queue.dev:getLinkStatus().speed
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
