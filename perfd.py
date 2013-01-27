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
from tempfile import mkdtemp

#from txsocksx.client import SOCKS5ClientEndpoint
from socksclient import SOCKSWrapper

import txtorcon

from twisted.application import service
from twisted.internet import defer, endpoints, reactor, task, interfaces
from twisted.web import client, resource, server
from twisted.python import log

from zope.interface import implements

class UrandomResource(resource.Resource):
    """ Pseudo-random data resource to be served via our web server.
        Maybe this code is overly complex, and we can instead write
        pseudo-random files to disk once and serve them with some
        standard Twisted foo. """
    implements(interfaces.IPullProducer)

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
        return server.NOT_DONE_YET

    def getChild(self, name, request):
        if len(name) > 7 or not name.isdigit():
            return resource.NoResource()
        else:
            self.size = int(name)
            return self

class PerfdWebHome(resource.Resource):
    def getChild(self, name, request):
        if name == 'urandom':
            return UrandomResource()
        else:
            return resource.NoResource()


class PerfdWebRequest(object):
    def __init__(self, host, http_port, socks_port, file_size):
        self.start = time.time()
        endpoint = endpoints.TCP4ClientEndpoint(reactor, host, http_port)
        wrapper = SOCKSWrapper(reactor, 'localhost', socks_port, endpoint)
        url = 'http://%s:%d/urandom/%d' % (host, http_port, file_size, )
        print 'Preparing request to "%s" via SOCKS on %d for %d bytes' % (url, socks_port, file_size)
        factory = client.HTTPClientFactory(url)
        factory.deferred.addCallback(self._printStatistics)
        deferred = wrapper.connect(factory)
        deferred.addCallback(self._connected)

    def _printStatistics(self, response):
        self.datacomplete = time.time()
        #print 'START=%.2f CONNECT=%.2f DATACOMPLETE=%.2f' % (self.start, self.connect, self.datacomplete, )
        log.msg('START=%.2f CONNECT=%.2f DATACOMPLETE=%.2f' % (self.start, self.connect, self.datacomplete))

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

    def _connected(self, proxy):
        self.connect = time.time()
        log.msg("connection at" + str(self.connect()))


class TorCircuitCreationService(service.Service, txtorcon.StreamListenerMixin, txtorcon.CircuitListenerMixin):
    implements(service.IService,
               txtorcon.IStreamAttacher,
               txtorcon.IStreamListener,
               txtorcon.ICircuitListener)

    port = 8080
    socks_port = 9050
    frequency = 300
    file_size = 51200
    public_ip = '127.0.0.1'             # testing, use real one (or host)
    public_ip = 'atlantis.meejah.ca'

    def __init__(self):
        self.tor_endpoint = endpoints.TCP4ClientEndpoint(reactor, 'localhost', 9052)
        self.resource = PerfdWebHome()
        self.outstanding_circuits = []
        '''Circuits we've asked to be built, but aren't yet complete (items are circuit IDs)'''

        self.completed_circuits = {}
        '''Circuits we've successfully built ourselves (key is circuit ID, value is Circuit object)'''

    def buildOneCircuit(self):
        d = 

    def startService(self):
       service.Service.startService(self)

       self.web_endpoint = endpoints.TCP4ServerEndpoint(reactor, self.port)
       self.web_endpoint.listen(server.Site(PerfdWebHome()))

       self._bootstrap().addCallback(self._complete).addErrback(self._error)

    def _error(self, fail):
        sys.stderr.write(fail.getBriefTraceback())
        return fail

    def _bootstrap(self):
        return txtorcon.build_tor_connection(endpoints.TCP4ClientEndpoint(reactor, 'localhost', 9051), build_state=False)


    def _updates(self, prog, tag, summary):
        log.msg('%d%%: %s' % (prog, summary))


    def _complete(self, proto):
        log.msg('Connected to Tor version %s' % proto.version)
        log.msg("Launching periodic requests every %f seconds" % self.frequency)
        if False:
            self.service_requestor = task.LoopingCall(PerfdWebRequest, self.config.HiddenServices[0].hostname,
                                                      self.port, self.socks_port, self.file_size)
            self.service_requestor.start(self.frequency)

        else:
            self.service_requestor = task.LoopingCall(PerfdWebRequest, self.public_ip,
                                                      self.port, self.socks_port, self.file_size)
            self.service_requestor.start(self.frequency)

    ## txtorcon.IStreamAttacher
    def attach_stream(stream, circuits):
        pass

    ## txtorcon.IStreamListener
    def stream_new(stream):
        "a new stream has been created"

    def stream_succeeded(stream):
        "stream has succeeded"

    def stream_attach(stream, circuit):
        "the stream has been attached to a circuit"

    def stream_detach(stream, reason):
        "the stream has been detached from its circuit"

    def stream_closed(stream):
        "stream has been closed (won't be in controller's list anymore)"

    def stream_failed(stream, reason, remote_reason):
        "stream failed for some reason (won't be in controller's list anymore)"

    ## txtorcon.ICircuitListener
    def circuit_new(circuit):
        """A new circuit has been created.  You'll always get one of
        these for every Circuit even if it doesn't go through the "launched"
        state."""

    def circuit_launched(circuit):
        "A new circuit has been started."

    def circuit_extend(circuit, router):
        "A circuit has been extended to include a new router hop."

    def circuit_built(circuit):
        """
        A circuit has been extended to all hops (usually 3 for user
        circuits).
        """

    def circuit_closed(circuit):
        """
        A circuit has been closed cleanly (won't be in controller's list any more).
        """

    def circuit_failed(circuit, reason):
        """A circuit has been closed because something went wrong.

        The circuit won't be in the TorState's list anymore. The
        reason comes from Tor (see tor-spec.txt). It is one of the
        following strings: MISC, RESOLVEFAILED, CONNECTREFUSED,
        EXITPOLICY, DESTROY, DONE, TIMEOUT, NOROUTE, HIBERNATING,
        INTERNAL,RESOURCELIMIT, CONNRESET, TORPROTOCOL, NOTDIRECTORY,
        END, PRIVATE_ADDR.

        However, don't depend on that: it could be anything.
        """

application = service.Application("perfd")
torservice = TorCircuitCreationService()
torservice.setServiceParent(application)
