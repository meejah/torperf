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

# more verbose output
DEBUG = False

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
        factory.deferred.addCallback(self._printStatistics).addErrback(self._error)
        deferred = wrapper.connect(factory)
        deferred.addCallback(self._connected)

    def _error(self, *args):
        sys.stderr.write(fail.getBriefTraceback())
        return fail

    def _printStatistics(self, response):
        self.datacomplete = time.time()
        #print 'START=%.2f CONNECT=%.2f DATACOMPLETE=%.2f' % (self.start, self.connect, self.datacomplete, )
        print "elapsed:", (self.datacomplete - self.start)
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
        log.msg("connection at " + str(self.connect))


class TorCircuitCreationService(service.Service, txtorcon.StreamListenerMixin, txtorcon.CircuitListenerMixin):
    implements(service.IService,
               txtorcon.IStreamAttacher,
               txtorcon.IStreamListener,
               txtorcon.ICircuitListener)

    port = 8080
    socks_port = 9050
    frequency = 60
    file_size = 51200
    public_ip = '127.0.0.1'             # testing, use real one (or host)
    public_ip = 'atlantis.meejah.ca'

    def __init__(self):
        self.tor_endpoint = endpoints.TCP4ClientEndpoint(reactor, 'localhost', 9052)
        self.tor_state = None
        self.resource = PerfdWebHome()
        self.outstanding_circuits = []
        '''Circuits we've asked to be built, but aren't yet complete (items are circuit IDs)'''

        self.completed_circuits = {}
        '''Circuits we've successfully built ourselves (key is circuit ID, value is Circuit object)'''

    def buildOneCircuit(self):
        if self.tor_state is None:
            raise RuntimeError("Tor connection not available yet.")

        sys.stderr.write('Requesting a circuit.\n')
        d = self.tor_state.build_circuit(None)
        d.addCallback(self._circuitUnderConstruction)

    def _circuitUnderConstruction(self, circ):
        if DEBUG:
            sys.stderr.write('Circuit being establised! ' + str(circ) + '\n')
        self.outstanding_circuits.append(circ)
        circ.start_time = time.time()

    def _newlyBuiltCircuit(self, circ):
        """circ is now in self.completed_circuits and was just built."""
        print "WE HAVE ONE of ours:", circ

    def startService(self):
       service.Service.startService(self)

       self.web_endpoint = endpoints.TCP4ServerEndpoint(reactor, self.port)
       self.web_endpoint.listen(server.Site(PerfdWebHome()))

       self._bootstrap().addCallback(self._complete).addErrback(self._error)

    def _error(self, fail):
        sys.stderr.write(fail.getBriefTraceback())
        return fail

    def _bootstrap(self):
        return txtorcon.build_tor_connection(endpoints.TCP4ClientEndpoint(reactor, 'localhost', 9051), build_state=True)


    def _updates(self, prog, tag, summary):
        log.msg('%d%%: %s' % (prog, summary))


    def _complete(self, state):
        self.tor_state = state
        self.tor_state.add_circuit_listener(self)
        self.tor_state.add_stream_listener(self)
        self.buildOneCircuit()

        log.msg('Connected to Tor version %s' % state.protocol.version)
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
    def attach_stream(self, stream, circuits):
        pass

    ## txtorcon.IStreamListener
    def stream_new(self, stream):
        "a new stream has been created"

    def stream_succeeded(self, stream):
        "stream has succeeded"

    def stream_attach(self, stream, circuit):
        "the stream has been attached to a circuit"
        sys.stderr.write('attach: %s -> %s\n' % (str(stream), str(circuit)))

    def stream_detach(self, stream, reason):
        "the stream has been detached from its circuit"

    def stream_closed(self, stream):
        "stream has been closed (won't be in controller's list anymore)"

    def stream_failed(self, stream, reason, remote_reason):
        "stream failed for some reason (won't be in controller's list anymore)"

    ## txtorcon.ICircuitListener
    def circuit_new(self, circuit):
        """A new circuit has been created.  You'll always get one of
        these for every Circuit even if it doesn't go through the "launched"
        state."""

    def circuit_launched(self, circuit):
        "A new circuit has been started."

    def circuit_extend(self, circuit, router):
        "A circuit has been extended to include a new router hop."
        if circuit in self.outstanding_circuits:
            sys.stderr.write('ONE OF OURS extended: %s %s\n' % (str(circuit), str(router)))

        if DEBUG:
            sys.stderr.write('Extend: %s %s\n' % (str(circuit), str(router)))

    def circuit_built(self, circuit):
        """
        A circuit has been extended to all hops (usually 3 for user
        circuits).
        """
        if circuit in self.outstanding_circuits:
            sys.stderr.write('OURS is built: %s\n' % str(circuit))
            self.completed_circuits[circuit.id] = circuit
            self.outstanding_circuits.remove(circuit)
            self._newlyBuiltCircuit(circuit)
            circuit.built_time = time.time()
            diff = circuit.built_time - circuit.start_time
            print "Circuit took %f seconds to build." % diff

        if DEBUG:
            sys.stderr.write('built: %s\n' % (str(circuit)))

    def circuit_closed(self, circuit):
        """
        A circuit has been closed cleanly (won't be in controller's list any more).
        """

    def circuit_failed(self, circuit, reason):
        """A circuit has been closed because something went wrong."""

        if DEBUG:
            sys.stderr.write('failed: %s %s\n' % (str(circuit), str(reason)))

        if circuit in self.outstanding_circuits:
            circuit.failed_time = time.time()
            diff = circuit.failed_time - circuit.start_time

            sys.stderr.write("One of our outstanding circuits failed!\n")
            print "Circuit took %f seconds to fail." % diff
            self.outstanding_circuits.remove(circuit)
            self.buildOneCircuit()

application = service.Application("perfd")
torservice = TorCircuitCreationService()
torservice.setServiceParent(application)

if __name__ == '__main__':
    print 'Please use "twistd -noy perfd.py" to launch perfd.py for debugging, or use the "perfd" shell script'
