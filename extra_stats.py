#!/usr/bin/python

import sys, time
import TorCtl.TorUtil as TorUtil
import TorCtl.TorCtl as TorCtl

HOST = "127.0.0.1"

class Circuit:
  def __init__(self, launch_time, circ_id):
    self.circ_id = circ_id
    self.launch_time = launch_time
    self.close_time = launch_time
    self.stream_end_time = 0
    self.path = []
    self.build_times = []
    self.failed = False
    self.fail_reason = None
    self.used = False
    self.strm_id = 0
    self.stream_failed = False
    self.stream_fail_reason = None

class WriteStats(TorCtl.PostEventListener):
  def __init__(self, port, filename):
    TorCtl.PostEventListener.__init__(self)
    self._port = int(port)
    self._filename = filename
    self._conn = None
    self.all_circs = {}
    self.ignore_streams = {}
    self.current_timeout = None
    self.current_quantile = None

  def connect(self):
    self._conn = TorCtl.connect(HOST, self._port)
    self._conn.debug(file("control.log", "w"))
    if not self._conn:
      sys.exit(2)

  def setup_listener(self):
    try:
      # buildtimeout_set will not be available in 0.2.1
      self._conn.set_events(["STREAM", "CIRC", "BUILDTIMEOUT_SET"],
                            extended=True)
    except:
      self._conn.set_events(["STREAM", "CIRC"], extended=True)
    self._conn.add_event_listener(self)

  def buildtimeout_set_event(self, b):
    self.current_timeout = b.timeout_ms
    self.current_quantile = b.cutoff_quantile
    result = b.event_name + " " +b.body
    self.write_result(result)

  def circ_status_event(self, c):
    if c.status == "LAUNCHED":
      self.all_circs[c.circ_id] = Circuit(c.arrived_at, c.circ_id)
    elif c.circ_id not in self.all_circs:
      return

    circ = self.all_circs[c.circ_id]
    if c.status == "EXTENDED":
      circ.build_times.append(c.arrived_at-circ.launch_time)
    elif c.status == "BUILT":
      circ.path = c.path
    elif c.status == "FAILED":
      circ.fail_reason = c.reason
      if c.remote_reason: circ.fail_reason += ":"+c.remote_reason
      circ.path = c.path
      circ.close_time = c.arrived_at
      circ.failed = True
      self.write_circ(circ)
      del self.all_circs[c.circ_id]
    elif c.status == "CLOSED":
      circ.close_time = c.arrived_at
      self.write_circ(circ)
      del self.all_circs[c.circ_id]

  def write_circ(self, circ):
    result = "CIRC_ID=%d LAUNCH=%s PATH=%s BUILDTIMES=%s " \
         % (circ.circ_id, str(circ.launch_time), ",".join(circ.path),
            ",".join(map(str, circ.build_times)))
    if circ.failed:
      result += "FAIL_REASONS=%s " % circ.fail_reason
    if circ.stream_failed:
      result += "STREAM_FAIL_REASONS=%s " % circ.stream_fail_reason
    elif circ.used:
      result += "USED_AT=%s USED_BY=%d " % \
          (str(circ.stream_end_time), circ.strm_id)

    if self.current_timeout:
      result += "TIMEOUT=%d QUANTILE=%f " % \
         (self.current_timeout, self.current_quantile)

    self.write_result(result)

  def stream_status_event(self, event):
    if event.status == "NEW":
      if event.purpose != "USER":
        self.ignore_streams[event.strm_id] = True
      return
    if event.strm_id in self.ignore_streams:
      if event.status == "CLOSED":
        del self.ignore_streams[event.strm_id]
      return
    if event.circ_id not in self.all_circs:
      if event.circ_id:
        TorUtil.plog("WARN",
           "Unknown circuit id %d has a stream event %d %s" % \
           (event.circ_id, event.strm_id, event.status))
      return
    circ = self.all_circs[event.circ_id]
    if event.status == 'DETACHED' or event.status == 'FAILED':
      # Detached usually means there was some failure
      assert not circ.strm_id
      circ.used = True
      circ.strm_id = event.strm_id
      circ.stream_failed = True
      circ.stream_end_time = event.arrived_at
      if event.reason:
        circ.stream_fail_reason = event.reason
        if event.remote_reason:
          circ.stream_fail_reason += " "+event.remote_reason
      self.write_circ(circ)
      # We have no explicit assurance here that tor will not
      # try to reuse this circuit later... But we should
      # print out a warn above if that happens.
      del self.all_circs[event.circ_id]
      # Some STREAM FAILED events are paired with a CLOSED, some are not :(
      if event.status == "FAILED":
        self.ignore_streams[event.strm_id] = True
    if event.status == 'CLOSED':
      assert not circ.strm_id or circ.stream_failed
      circ.used = True
      circ.strm_id = event.strm_id
      circ.stream_end_time = event.arrived_at
      self.write_circ(circ)
      del self.all_circs[event.circ_id]

  def write_result(self, result):
    # XXX: hrmm. seems wasteful to keep opening+closing..
    statsfile = open(self._filename, 'a')
    statsfile.write(result+"\n")
    statsfile.close()

def main():
  if len(sys.argv) < 3:
    print "Bad arguments"
    sys.exit(1)

  port = sys.argv[1]
  filename = sys.argv[2]

  stats = WriteStats(port, filename)
  stats.connect()
  stats.setup_listener()
  try:
    # XXX: join instead
    while True:
      time.sleep(500)
  except:
    pass

if __name__ == '__main__':
  main()

