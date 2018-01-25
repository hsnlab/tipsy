--- Replay a pcap file and measure latencies.

local ffi = require "ffi"

local mg      = require "moongen"
local device  = require "device"
local memory  = require "memory"
local stats   = require "stats"
local log     = require "log"
local pcap    = require "pcap"
local hist    = require "histogram"
local ts      = require "timestamping"
local timer   = require "timer"
local dpdk       = require "dpdk"

function configure(parser)
   parser:description("Replay a PCAP file with rate control and measure latencies.")
   parser:argument("txDev", "txport[:numcores]"):default(0)
   parser:argument("rxDev", "rxport"):default(1):convert(tonumber)
   parser:argument("file", "pcap file"):args(1)
   parser:option("--rate-limit", "replay speed [Mbit/s]\ndefault, 0: replay as fast as possible\n(Relies on hw rate limiting of txDev: see test-setRate.lua)"):default(0):convert(tonumber):target("rateLimit")
   parser:option("-h --hfile", "latency histogram."):default("histogram.csv")
   parser:option("-r --runtime", "running time in seconds."):default(0):convert(tonumber)
   parser:flag("-l --loop", "repeat pcap file")
   parser:flag("-t --timestamps", "add timestamps to a pcap stream to measure latency")
   parser:option("-o --ofile", "file prefix to use for saving the results\n"
                 .. "($prefix.throughput.csv and $prefix.latency.csv)"):default(nil)
   local args = parser:parse()
   return args
end

function master(args)
   local txport, cores
   if args.txDev:find(":") then
      txport, cores = tonumberall(args.txDev:match("(%d+):(%d+)"))
   else
      txport, cores = tonumber(args.txDev), 1
   end
   local txDev, rxDev, lastRxQue
   if txport ~= args.rxDev then
     txDev = device.config({port = txport, txQueues = cores+1, rxQueues = 2})
     rxDev = device.config({port = args.rxDev, rxQueues = cores+1, txQueues = 2})
     lastRxQue = cores
   else
      txDev = device.config({port = txport,
                             txQueues = cores+1, rxQueues = cores+1})
      rxDev = txDev
      lastRxQue = cores
   end
   device.waitForLinks()
   if args.rateLimit > 0 then
      log:info('Set hw rate-limit of %s to %s Mbit/s', txDev, args.rateLimit)
      txDev:setRate(args.rateLimit)
   end
   for i = 1, cores do
      mg.startTask("replay_pcap", txDev:getTxQueue(i-1),
                   args.file, args.loop)
   end
   if args.ofile then
      stats.startStatsTask{txDevices = {txDev}, rxDevices = {rxDev},
                           format="csv", file=args.ofile .. ".throughput.csv"}
   else
      stats.startStatsTask{txDevices = {txDev}, rxDevices = {rxDev},
                           format="plain"}
   end
   if args.timestamps then
      mg.startSharedTask("measure_latency", txDev:getTxQueue(cores),
                         rxDev:getRxQueue(lastRxQue), args.hfile,
                         args.file, args.ofile)
   end
   if args.runtime > 0 then
      mg.setRuntime(args.runtime)
   end
   mg.waitForTasks()
end

function replay_pcap(queue, file, loop)
   local mempool = memory:createMemPool(4096)
   local bufs = mempool:bufArray()
   local pcapFile = pcap:newReader(file)
   local prev = 0
   local linkSpeed = queue.dev:getLinkStatus().speed
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
   end
end

function measure_latency(txQueue, rxQueue, histfile, file, ofile)
   local timestamper = ts:newTimestamper(txQueue, rxQueue, nil, true)
   local hist = hist:new()
   -- local mac_dst = "68:05:ca:30:50:70"

   local mempool = memory:createMemPool(4096)
   local bufs = mempool:bufArray()
   local pcapFile = pcap:newReader(file)

   local n = pcapFile:read(bufs)
   local m = 0
   local size_warning = false

   mg.sleepMillis(1000) -- ensure that the load task is running
   while mg.running() do
      hist:update(timestamper:measureLatency(
        400,
        function(buf)
           m = m + 1
           if m >= n then
              m = 0
           end
           local sample = bufs.array[m]:getEthernetPacket()
           local pkt = buf:getEthernetPacket()
           if true then
              -- pkt.eth.dst:setString(mac_dst)
              pkt.eth:setDst( sample.eth:getDst() )
              pkt.eth:setSrc( sample.eth:getSrc() )

              -- pkt.eth:setType(2048)
              -- print(pkt.eth:getType())

              sample = bufs.array[m]:getIPPacket()
              if sample then
                 pkt = buf:getIPPacket()
                 pkt.ip4.src:set( sample.ip4.src:get() )
                 pkt.ip4.dst:set( sample.ip4.dst:get() )
                 pktSize = sample.ip4:getLength()
                 if pktSize < 76 then
                    pktSize = 76
                    if not size_warning then
                       log:warn('[Timestamping] Packet size increased to 76 bytes')
                       size_warning = true
                    end
                 end

                 -- buf:getUdpPacket().udp:setDstPort(3190)

                 buf:getUdpPtpPacket():setLength(pktSize)
                 buf.data_len = pktSize
              end
           else
              ffi.copy(buf:getData(), bufs.array[m]:getData(), bufs.array[m]:getSize())
              -- buf:getEthernetPacket().eth:setType(35063)
              buf:getUdpPacket().udp:setSrcPort(319)
              buf:getUdpPacket().udp:setDstPort(319)
           end
        end))
   end
   hist:print()
   hist:save(histfile)
   local prefix = 'X'
   log:warn("%sSamples: %d, Average: %.1f ns, StdDev: %.1f ns, Quartiles: %.1f/%.1f/%.1f ns",
            prefix and ("[" .. prefix .. "] ") or "",
            hist.numSamples, hist.avg, hist.stdDev, unpack(hist.quarts))
   if ofile then
      file = io.open(ofile .. ".latency.csv", "w")
      file:write("Samples,Average,StdDev,1st_Quartiles,2nd_Quartiles,3rd_Quartiles\n")
      file:write(string.format("%d,%.1f,%.1f,%.1f,%.1f.%.1f\n",
                               hist.numSamples, hist.avg, hist.stdDev, unpack(hist.quarts)))
      file:close()
   end
end
