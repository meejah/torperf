#!/usr/bin/python
#
# This script consolidates a .data file and an .extradata file together,
# matching the lines based on the completion time.
#
# The resulting output will be the union of both files. It will match lines
# where possible, and include unmatched lines from both files as well.
#
# Usage:
#   ./consolidate-stats.py <.data file> <.extradata file> <.mergedata file>
###

import sys

class Data:
  def __init__(self, filename, mode="r"):
    self._filename = filename
    self._file = open(filename, mode)
    self._curData = None
    self._retCurrent = False

  def prepline(self):
    if self._retCurrent:
      self._retCurrent = False
      return self._curData
    line = self._file.readline()
    if line == "" or line == "\n":
      raise StopIteration
    line = line.strip()
    self._curData = line.split(" ")
    return self._curData

  def keepCurrent(self):
    self._retCurrent = True

class TorperfData(Data):
  def __init__(self, filename):
    Data.__init__(self, filename)
    self.fields = "STARTSEC STARTUSEC SOCKETSEC SOCKETUSEC CONNECTSEC CONNECTUSEC NEGOTIATESEC NEGOTIATEUSEC REQUESTSEC REQUESTUSEC RESPONSESEC RESPONSEUSEC DATAREQUESTSEC DATAREQUESTUSEC DATARESPONSESEC DATARESPONSEUSEC DATACOMPLETESEC DATACOMPLETEUSEC WRITEBYTES READBYTES DIDTIMEOUT".split(" ")

  def next(self):
    ret = {}
    values = self.prepline()
    for i in xrange(len(values)):
      ret[self.fields[i]] = values[i]
    return ret

  def __iter__(self):
    return self

class ExtraData(Data):
  def __init__(self, filename):
    Data.__init__(self, filename)

  def next(self):
    cont = self.prepline()

    ret = {}
    for i in cont:
      if not "=" in i:
        ret[i] = ""
        continue
      pair = i.split("=")
      ret[pair[0]] = pair[1]

    if not "CIRC_ID" in ret:
      #print('Ignoring line "' + " ".join(cont) + '"')
      return self.next()
    return ret

  def __iter__(self):
    return self

class MergeData(Data):
  def __init__(self, filename):
    Data.__init__(self, filename, "w")

  def writeLine(self, data):
    line = []
    for key in data.iterkeys():
      line.append(key+"="+data[key])
    line.sort()
    self._file.write(" ".join(line) + "\n")

def main():
  if len(sys.argv) != 4:
    print("See script header for usage")
    sys.exit(1)

  torperfdata = TorperfData(sys.argv[1])
  extradata = ExtraData(sys.argv[2])
  mergedata = MergeData(sys.argv[3])
  slack = 1.0 # More than 1s means something is really, really wrong
  lastDataTime = 0
  lastExtraTime = 0
  dataLine = 0
  extraLine = 0
  mergedYet = False
  for data in torperfdata:
    dataLine += 1
    dataEndTime = int(data["DATACOMPLETESEC"])
    dataEndTime += int(data["DATACOMPLETEUSEC"])/1000000.0
    if not dataEndTime:
      # Skip failures
      continue

    if lastDataTime > dataEndTime:
      print "Torperf .data is not monotonic! Sort it by completion time!"
      print "Line "+str(dataLine)+" "+str(lastDataTime)+" > "+str(dataEndTime)
      sys.exit(0)
    lastDataTime = dataEndTime
    for extra in extradata:
      extraLine += 1
      if not "USED_AT" in extra or not extra["USED_AT"]:
        mergedata.writeLine(extra)
        continue # Failed circ

      extraEndTime = float(extra["USED_AT"])
      if lastExtraTime > extraEndTime:
        print "The .extradata is not monotonic! Sort it by USED_AT!"
        print "Line "+str(extraLine)+" "+str(lastExtraTime)+" > "+str(extraEndTime)
        sys.exit(0)
      lastExtraTime = extraEndTime
      if abs(dataEndTime - extraEndTime) > slack:
        if dataEndTime < extraEndTime:
          if mergedYet:
            print("Got a data line at "+str(dataLine)+ " without extradata (line "+str(extraLine)+")")
          extradata.keepCurrent()
          extraLine -= 1
          mergedata.writeLine(data)
        else:
          torperfdata.keepCurrent()
          dataLine -= 1
          mergedata.writeLine(extra)
        break

      mergedYet = True
      data.update(extra)
      mergedata.writeLine(data)
      break


if __name__ == "__main__":
  main()
