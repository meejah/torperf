"""
Proof-of-concept implementation of Torperf using Twisted:

- Sets up an HTTP server that serves random data for performance
  tests.

- Periodically requests 50 KiB of data from its own HTTP server via
  Tor (or a local SOCKS proxy in general) and logs timestamps.
"""

import os
import sys
import time

from socksclient import SOCKSv4ClientProtocol, SOCKSWrapper

from twisted.application.internet import TimerService
from twisted.application import internet, service
from twisted.internet import defer, endpoints, protocol, reactor, task
from twisted.internet.defer import Deferred
from twisted.internet.interfaces import IPullProducer
from twisted.internet.protocol import Protocol, ClientFactory
from twisted.protocols.basic import FileSender
from twisted.web.client import HTTPClientFactory
from twisted.web.error import NoResource
from twisted.web.resource import Resource
from twisted.web.server import NOT_DONE_YET, Site
from twisted.python import log

from zope.interface import implements

class UrandomResource(Resource):
    """ Pseudo-random data resource to be served via our web server.
        Maybe this code is overly complex, and we can instead write
        pseudo-random files to disk once and serve them with some
        standard Twisted foo. """
    implements(IPullProducer)

    def beginProducing(self, consumer):
        self.consumer = consumer
        self.deferred = deferred = defer.Deferred()
        self.consumer.registerProducer(self, False)
        return deferred

    def resumeProducing(self):
        if self.size <= 0:
            self.consumer.unregisterProducer()
            if self.deferred:
                self.deferred.callback('o')
                self.deferred = None
            return
        chunk_size = min(self.size, 2 ** 16)
        self.size -= chunk_size
        self.consumer.write(os.urandom(chunk_size))

    def stopProducing(self):
        if self.deferred:
            self.consumer.unregisterProducer()
            self.deferred.errback(
                Exception("Consumer asked us to stop producing."))
            self.deferred = None

    def render_GET(self, request):
        request.setHeader('Content-Type', 'application/octet-stream')
        d = self.beginProducing(request)
        def err(ignored):
            Exception("Error")
        def cbFinished(ignored):
            request.finish()
        d.addErrback(err).addCallback(cbFinished)
        return NOT_DONE_YET

    def getChild(self, name, request):
        if len(name) > 7 or not name.isdigit():
            return NoResource()
        else:
            self.size = int(name)
            return self

class PerfdWebHome(Resource):
    def getChild(self, name, request):
        if name == 'urandom':
            return UrandomResource()
        else:
            return NoResource()

class PerfdWebServer(object):
    def __init__(self, application, http_port):
        """ Create our web server and register it with the Twisted
            application. """
        resource = PerfdWebHome()
        site = Site(resource)
        perfdService = internet.TCPServer(http_port, site)
        perfdService.setServiceParent(application)

class PerfdWebRequest(object):
    def __init__(self, host, http_port, socks_port, file_size):
        self.start = time.time()
        endpoint = endpoints.TCP4ClientEndpoint(reactor, host, http_port)
        wrapper = SOCKSWrapper(reactor, 'localhost', socks_port, endpoint)
        url = 'http://%s:%d/urandom/%d' % (host, http_port, file_size, )
        factory = HTTPClientFactory(url)
        factory.deferred.addCallback(self.printResource)
        deferred = wrapper.connect(factory)
        deferred.addCallback(self.wrappercb)

    def printResource(self, response):
        self.datacomplete = time.time()
        print 'START=%.2f CONNECT=%.2f DATACOMPLETE=%.2f' % (self.start, self.connect, self.datacomplete, )

        """ Eventually support most or all of these:
            START=1338357901.42 # Connection process started
            SOCKET=1338357901.42 # After socket is created
            CONNECT=1338357901.42 # After socket is connected
            NEGOTIATE=1338357901.42 # After authentication methods are negotiated (SOCKS 5 only)
            REQUEST=1338357901.42 # After SOCKS request is sent
            RESPONSE=1338357901.83 # After SOCKS response is received
            DATAREQUEST=1338357901.83 # After HTTP request is written
            DATARESPONSE=1338357902.25 # After first response is received
            DATACOMPLETE=1338357902.91 # After payload is complete
            WRITEBYTES=75 # Written bytes
            READBYTES=51442 # Read bytes
            DIDTIMEOUT=0 # Timeout (optional field)
            DATAPERC10=1338357902.48 # After 10% of expected bytes are read (optional field)
            DATAPERC20=1338357902.48 # After 20% of expected bytes are read (optional field)
            DATAPERC30=1338357902.61 # After 30% of expected bytes are read (optional field)
            DATAPERC40=1338357902.64 # After 40% of expected bytes are read (optional field)
            DATAPERC50=1338357902.65 # After 50% of expected bytes are read (optional field)
            DATAPERC60=1338357902.74 # After 60% of expected bytes are read (optional field)
            DATAPERC70=1338357902.74 # After 70% of expected bytes are read (optional field)
            DATAPERC80=1338357902.75 # After 80% of expected bytes are read (optional field)
            DATAPERC90=1338357902.79 # After 90% of expected bytes are read (optional field)
        """

    def wrappercb(self, proxy):
        self.connect = time.time()

class PeriodicRequestService(object):
    def __init__(self, application, frequency, host, http_port,
            socks_port, file_size):
        self.host = host
        self.http_port = http_port
        self.socks_port = socks_port
        self.file_size = file_size
        ts = TimerService(frequency, self.makeRequest)
        ts.setServiceParent(application)

    def makeRequest(self):
        PerfdWebRequest(self.host, self.http_port, self.socks_port,
            self.file_size)

PUBLIC_IP = '127.0.0.1' # For testing only, put in real public IP
HTTP_PORT = 8080
SOCKS_PORT = 1080 # For testing only, set to 9050 for Tor
FREQUENCY = 300
FILE_SIZE=51200

application = service.Application("perfd")
PerfdWebServer(application, HTTP_PORT)
PeriodicRequestService(application, FREQUENCY, PUBLIC_IP, HTTP_PORT,
    SOCKS_PORT, FILE_SIZE)

