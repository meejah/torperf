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
#from tempfile import mkdtemp

#from txsocksx.client import SOCKS5ClientEndpoint
from socksclient import SOCKSWrapper

import txtorcon

from twisted.application import service
from twisted.internet import defer, endpoints, reactor, task, interfaces
from twisted.web import client, resource, server
from twisted.python import log, usage
from twisted.plugin import IPlugin

from zope.interface import implements

# more verbose output
DEBUG = False

class Options(usage.Options):
    """
    command-line options we understand
    """

    optParameters = [
        ['connect', 'c', None, 'Tor control socket to connect to in host:port format, like "localhost:9051" (the default).'],
        ['delay', 'n', 60, 'Seconds between performance tests.', int],
        ['port', 'p', 8080, 'Port to contact on the server.', int],
        ['socks-port', 's', 9050, 'Port of the SOCKS proxy to use.', int],
        ['file-size', 'f', 51200, 'Size of the file the server will serve.', int],
        ['public-host', 'h', '127.0.0.1', 'Public IP address or hostname the server will be reachable at.'],
        ['debug-txtorcon', 'D', False, "Turn on txtorcon's debug log."]
        ]


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


class MeasuringHTTPPageGetter(client.HTTPPageGetter):

    def __init__(self):
        self.times = {}
        self.timer = interfaces.IReactorTime(reactor)
        self.sentBytes = 0
        self.receivedBytes = 0
        self.expectedBytes = 51200
        self.decileLogged = 0

    def connectionMade(self):
        client.HTTPPageGetter.connectionMade(self)
        self.times['DATAREQUEST'] = self.timer.seconds()

    def sendCommand(self, command, path):
        self.sentBytes += len('%s %s HTTP/1.0\r\n' % (command, path))
        client.HTTPPageGetter.sendCommand(self, command, path)

    def sendHeader(self, name, value):
        self.sentBytes += len('%s: %s\r\n' % (name, value))
        client.HTTPPageGetter.sendHeader(self, name, value)

    def endHeaders(self):
        self.sentBytes += len('\r\n')
        client.HTTPPageGetter.endHeaders(self)

    def dataReceived(self, data):
        if self.receivedBytes == 0 and len(data) > 0:
            self.times['DATARESPONSE'] = self.timer.seconds()
        self.receivedBytes += len(data)
        while (self.receivedBytes < self.expectedBytes and
              (self.receivedBytes * 10) / self.expectedBytes >
               self.decileLogged):
            self.decileLogged += 1
            self.times['DATAPERC%d' % (self.decileLogged * 10, )] = \
                       self.timer.seconds()
        client.HTTPPageGetter.dataReceived(self, data)

    def handleResponse(self, response):
        self.times['WRITEBYTES'] = self.sentBytes
        self.times['READBYTES'] = self.receivedBytes
        self.times['DATACOMPLETE'] = self.timer.seconds()
        self.times['DIDTIMEOUT'] = 0
        log.msg(self.times) # TODO How can we link these timestamps and
                            # other data to the PerfdWebRequest instance
                            # and store them in the database together with
                            # other request data, e.g., SOCKS timestamps?
        client.HTTPPageGetter.handleResponse(self, response)

    def timeout(self):
        self.times['DIDTIMEOUT'] = 1
        log.msg(self.times) # TODO How can we link these timestamps and
                            # other data to the PerfdWebRequest instance
                            # and store them in the database together with
                            # other request data, e.g., SOCKS timestamps?
        client.HTTPPageGetter.timeout(self)


class PerfdWebRequest(object):
    def __init__(self, host, http_port, socks_port, file_size):
        self.times = {}
        self.timer = interfaces.IReactorTime(reactor)
        endpoint = endpoints.TCP4ClientEndpoint(reactor, host, http_port)
        wrapper = SOCKSWrapper(reactor, 'localhost', socks_port, endpoint,
                               self.times)
        url = 'http://%s:%d/urandom/%d' % (host, http_port, file_size, )
        timeout = 2  # TODO change to something reasonable after testing
        factory = client.HTTPClientFactory(url, timeout=timeout)
        factory.protocol = MeasuringHTTPPageGetter
        factory.deferred.addCallbacks(self._request_finished)
        deferred = wrapper.connect(factory)  # TODO use timeout, and use
                                             # remaining timeout for HTTP
                                             # request
        deferred.addCallbacks(self._request_finished)

    def _request_finished(self, ignored):
        log.msg(self.times)


class TorCircuitCreationService(service.Service):
    implements(service.IService)

    def __init__(self, options):
        self.port = options['port']
        self.socks_port = options['socks-port']
        self.frequency = options['delay']
        self.file_size = options['file-size']
        self.public_ip = options['public-host']
        if options['debug-txtorcon']:
            txtorcon.log.debug_logging()

        self.tor_state = None
        self.outstanding_circuits = []
        '''Circuits we've asked to be built, but aren't yet complete (items are circuit IDs)'''

        self.completed_circuits = {}
        '''Circuits we've successfully built ourselves (key is circuit ID, value is Circuit object)'''

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

class TorPerfdPlugin(object):
    implements(IPlugin, service.IServiceMaker)

    tapname = 'torperfd'
    description = 'Measure the performance of the Tor network.'
    options = Options

    def makeService(self, options):
        return TorCircuitCreationService(options)

serviceMaker = TorPerfdPlugin()

if __name__ == '__main__':
    print 'Please use "twistd -n torperfd" to launch perfd.py for debugging, or use the "perfd" shell script'

