#!/usr/bin/python
#
# This script takes a list of extradata files and tells you some statistics
# about the guard selection used by checking against the current consensus.
#
# Use the script like this:
# ./analyze_guards.py slowratio50kb.extradata slowratio1mb50kb.extradata
#
# It should then print out ranking stats one per file. Use your brain to
# determine if these stats make sense for the run you selected.

import sys
import math

import TorCtl.TorCtl
import TorCtl.TorUtil

TorCtl.TorUtil.loglevel = "NOTICE"
HOST="127.0.0.1"
PORT=9051

def analyze_list(router_map, idhex_list):
  min_rank = len(router_map)
  tot_rank = 0
  max_rank = 0
  absent = 0
  for idhex in idhex_list:
    if idhex not in router_map:
      absent += 1
      continue

    rank = router_map[idhex].list_rank
    tot_rank += rank
    if rank < min_rank: min_rank = rank
    if rank > max_rank: max_rank = rank

  avg = float(tot_rank)/(len(idhex_list)-absent)
  varience = 0

  for idhex in idhex_list:
    if idhex not in router_map: continue
    rank = router_map[idhex].list_rank
    varience += (rank-avg)*(rank-avg)

  return (min_rank, avg, math.sqrt(varience/(len(idhex_list)-absent-1)), max_rank, absent)

def process_file(router_map, file_name):
  f = file(file_name, "r")
  idhex_list = f.readlines()
  guard_list = []

  for i in xrange(len(idhex_list)):
    line = idhex_list[i].split()
    path = None
    used = False
    for word in line:
      if word.startswith("PATH="): path = word[5:]
      if word.startswith("USED_BY"): used = True

    if path and used:
      guard = path.split(",")
      guard_list.append(guard[0])

  print "Guard rank stats (min, avg, dev, total, absent): "
  print file_name + ": " + str(analyze_list(router_map, guard_list))


def main():
  c = TorCtl.TorCtl.connect(HOST, PORT)
  sorted_rlist = filter(lambda r: r.desc_bw > 0, c.read_routers(c.get_network_status()))
  router_map = {}
  for r in sorted_rlist: router_map["$"+r.idhex] = r

  if "ratio" in sys.argv[1]:
    print "Using ratio rankings"
    def ratio_cmp(r1, r2):
      if r1.bw/float(r1.desc_bw) > r2.bw/float(r2.desc_bw):
        return -1
      elif r1.bw/float(r1.desc_bw) < r2.bw/float(r2.desc_bw):
        return 1
      else:
        return 0
    sorted_rlist.sort(ratio_cmp)
  else:
    print "Using consensus bw rankings"
    sorted_rlist.sort(lambda x, y: cmp(y.bw, x.bw))

  for i in xrange(len(sorted_rlist)): sorted_rlist[i].list_rank = i

  for file_name in sys.argv[1:]:
    process_file(router_map, file_name)


if __name__ == '__main__':
  main()

