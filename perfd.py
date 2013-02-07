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
import functools

#from txsocksx.client import SOCKS5ClientEndpoint
from socksclient import SOCKSWrapper

import txtorcon

from twisted.application import service
from twisted.internet import defer, endpoints, reactor, task, interfaces
from twisted.web import client, resource, server
from twisted.python import log, usage
from twisted.plugin import IPlugin
from twisted.trial import unittest

from zope.interface import implements

# more verbose output
DEBUG = False

class Options(usage.Options):
    """
    command-line options we understand
    """

    optParameters = [
#        ['connect', 'c', None, 'Tor control socket to connect to in host:port format, like "localhost:9051" (the default).'],
        ['concurrent', 'c', 1, 'Number of slave Tor processes to launch.', int],
        ['delay', 'n', 60, 'Seconds between performance tests.', int],
        ['port', 'p', 80, 'Port to contact on the server.', int],
        ['socks-port', 's', 9050, 'Port of the SOCKS proxy to use.', int],
        ['file-size', 'f', 51200, 'Size of the file the server will serve.', int],
        ['public-host', 'h', '127.0.0.1', 'Public IP address or hostname the server will be reachable at.'],
        ['debug-txtorcon', 'D', False, "Turn on txtorcon's debug log."]
        ]

class RandomDataProducer(object):
    implements(interfaces.IPullProducer)

    def __init__(self, size, stats, ireactortime):
        self.size = size
        self.stats = stats
        self.timer = interfaces.IReactorTime(ireactortime)

    def beginProducing(self, consumer):
        self.remaining = self.size
        self.consumer = consumer
        self.deferred = defer.Deferred()
        self.consumer.registerProducer(self, False)
        return self.deferred

    def resumeProducing(self):
        if self.remaining <= 0:
            self.consumer.unregisterProducer()
            if self.deferred:
                self.deferred.callback('o')
                self.deferred = None
            return
        chunk_size = min(self.remaining, 2 ** 16)
        percentBefore = float(self.remaining) / float(self.size)
        self.remaining -= chunk_size
        percentAfter = float(self.remaining) / float(self.size)
        self.consumer.write(os.urandom(chunk_size))
        if percentBefore > 0.50 and percentAfter < 0.50:
            self.stats['SERVER_PERC50'] = self.timer.seconds()
            print "sent 50% of content from server", self.timer.seconds()

    def stopProducing(self):
        if self.deferred:
            self.consumer.unregisterProducer()
            self.deferred.errback(
                Exception("Consumer asked us to stop producing."))
            self.deferred = None


class UrandomResource(resource.Resource):
    """ Pseudo-random data resource to be served via our web server.
        Maybe this code is overly complex, and we can instead write
        pseudo-random files to disk once and serve them with some
        standard Twisted foo. """

    def __init__(self, size):
        self.size = size;
        print "created resource of size", self.size

    def render_GET(self, request):
        request.setHeader('Content-Type', 'application/octet-stream')
        ## fixme, pass a real stats object (i.e. same hashtable as the others)
        dataProducer = RandomDataProducer(self.size, {}, reactor)
        d = dataProducer.beginProducing(request)
        def err(ignored):
            Exception("Error")
        def cbFinished(ignored):
            request.finish()
        d.addErrback(err).addCallback(cbFinished)
        return server.NOT_DONE_YET


class UrandomResourceDispatcher(resource.Resource):
    children = {}

    def getChild(self, name, request):
        ## it's faster (and "more pythonic") to catch the exception
        ## rather than check the hashtable first

        try:
            size = int(name)
            if size > 999999:
                return resource.NoResource()

            try:
                return self.children[size]

            except KeyError:
                self.children[size] = UrandomResource(size)
                return self.children[size]

        except ValueError:
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
        timeout = 5  # TODO change to something reasonable after testing
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

    def __init__(self, reactor, options):
        self.port = options['port']
        self.socks_port = options['socks-port']
        self.frequency = options['delay']
        self.file_size = options['file-size']
        self.public_ip = options['public-host']
        if options['debug-txtorcon']:
            txtorcon.log.debug_logging()
        self.slave_tor_count = options['concurrent']

        self.slave_tors = []
        """All the Tor instances we have launched."""

    def _addSlave(self, config, slave):
        print "Slave successfully launched!", config, slave
        self.slave_tors.append(slave.protocol)
        self._complete(slave.protocol)

    def privilegedStartService(self):
        service.Service.privilegedStartService(self)

        self.web_endpoint = endpoints.TCP4ServerEndpoint(reactor, self.port)
        root = resource.Resource()
        root.putChild('urandom', UrandomResourceDispatcher())
        self.web_endpoint.listen(server.Site(root))

    def startService(self):
        service.Service.startService(self)
        for x in xrange(self.slave_tor_count):
            config = txtorcon.TorConfig()
            config.SocksPort = self.socks_port + x
            config.ControlPort = 9052 + x
            updates = functools.partial(self._updates, 'TOR-%d' % x)
            d = txtorcon.launch_tor(config, reactor,
                                    tor_binary='/usr/local/bin/tor',
                                    progress_updates=updates)
            d.addCallback(self._addSlave, config).addErrback(self._error)

    def _error(self, fail):
        sys.stderr.write(fail.getBriefTraceback())
        return fail

    def _updates(self, tor, prog, tag, summary):
        log.msg('%s: %d%%: %s' % (tor, prog, summary))

    def _complete(self, protocol):
        log.msg('Connected to Tor version %s' % protocol.version)
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
        return TorCircuitCreationService(reactor, options)

serviceMaker = TorPerfdPlugin()

if __name__ == '__main__':
    print 'Please use "twistd -n torperfd" to launch perfd.py for debugging, or use the "perfd" shell script'

class TestLaunch(unittest.TestCase):

    def test_launch(self):
        pass

