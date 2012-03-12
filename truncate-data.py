#!/usr/bin/env python
import os
import re
import sys
import time

# Truncate Torperf .data file by deleting lines older than 4 days, but
# only truncate once a week.
def main():

  # Check usage.
  if len(sys.argv) != 2:
    print "Usage: ./truncate.py <.data file>"
    return
  data_path = sys.argv[1]
  if not os.path.isfile(data_path):
    print "%s is not a .data file." % data_path
    return

  # Prepare for parsing.
  parselines = False
  copylines = False
  databak_path = data_path + ".bak"
  databak_file = None
  started_re = re.compile('(^[\\d]*) .*')
  now = time.time()

  # Parse the .data file line by line, possibly stopping early if the
  # first timestamp we find isn't older than a week.
  with open(data_path) as data_file:
    for line in data_file:

      # Copy lines written in the past 4 days.  We have already decided to
      # copy this part of the .data file before, so just copy the line and
      # continue.
      if copylines:
        databak_file.write(line)
        continue

      # Skip empty lines.
      if line.strip() == "":
        continue

      # Extract the first timestamp from the current line.
      m = started_re.match(line)
      if not m:
        print "%s is not a valid .data file." % data_path
        return
      started_ts = int(m.group(1))

      # Decide whether to start copying lines.  We have already decided to
      # truncate this file before.
      if parselines:
        if started_ts >= now - 4 * 24 * 60 * 60:
          databak_file.write(line)
          copylines = True
        continue

      # Decide whether to truncate this file at all.
      if started_ts >= now - 7 * 24 * 60 * 60:
        return

      # Open a .bak file to write into and start parsing lines to copy in
      # the next iteration.
      databak_file = open(databak_path, "w")
      parselines = True

  # Close the .bak file and replace the original .data file with it.
  if databak_file:
    databak_file.close()
    os.rename(databak_path, data_path)

if __name__ == "__main__":
  main()

