"""
Proof-of-concept implementation of Torperf using Twisted:

- Sets up an HTTP server that serves random data for performance
  tests.

- Periodically requests 50 KiB, 1 MiB, and 5 MiB of data from its own
  HTTP server via Tor and logs timestamps.
"""

server_config = {
    'public-host': '23.22.45.179',  # Public IP address or hostname the server will be reachable at.
    'http-port': 80,  # Port to contact on the server.
    'debug-txtorcon': False,  # Turn on txtorcon's debug log.
}

client_configs = [
    {
        'source': 'ec2',  # Source name.
        'launch-tor': True,  # Launch a Tor process.
        'tor-binary': '/usr/local/bin/tor',  # Tor binary to launch.
        'start-delay': 0,  # Seconds before first request, after web server and Tor process are available.
        'request-delay': 300,  # Seconds between requests.
        'socks-port': 9020,  # SOCKS port of the Tor process.
        'control-port': 10020,  # Control port of the Tor process.
        'file-size': 51200,  # Request size in bytes.
        'request-timeout': 295,  # Request timeout in seconds.
    },
    {
        'source': 'ec2',  # Source name.
        'launch-tor': True,  # Launch a Tor process.
        'tor-binary': '/usr/local/bin/tor',  # Tor binary to launch.
        'start-delay': 120,  # Seconds before first request, after web server and Tor process are available.
        'request-delay': 1800,  # Seconds between requests.
        'socks-port': 9021,  # SOCKS port of the Tor process.
        'control-port': 10021,  # Control port of the Tor process.
        'file-size': 1048576,  # Request size in bytes.
        'request-timeout': 1795,  # Request timeout in seconds.
    },
#    {
#        'source': 'ec2',  # Source name.
#        'launch-tor': True,  # Launch a Tor process.
#        'tor-binary': '/usr/local/bin/tor',  # Tor binary to launch.
#        'start-delay': 480,  # Seconds before first request, after web server and Tor process are available.
#        'request-delay': 3600,  # Seconds between requests.
#        'socks-port': 9022,  # SOCKS port of the Tor process.
#        'control-port': 10022,  # Control port of the Tor process.
#        'file-size': 5242880,  # Request size in bytes.
#        'request-timeout': 3595,  # Request timeout in seconds.
#    },
]

import os
import sys
import time
import functools

from socksclient import SOCKSWrapper

import txtorcon

from twisted.application import service
from twisted.internet import defer, endpoints, reactor, task, interfaces
from twisted.web import client, resource, server
from twisted.python import log, usage
from twisted.plugin import IPlugin
from twisted.trial import unittest

from zope.interface import implements


class Options(usage.Options):
    """
    command-line options we understand
    """

    optParameters = []


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
        try:
            size = int(name)
            if size > 9999999:
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
        # TODO Learn expected bytes from PerfdWebRequest instance
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
    def __init__(self, host, http_port, socks_port, file_size,
                 request_timeout):
        self.times = {}
        self.timer = interfaces.IReactorTime(reactor)
        endpoint = endpoints.TCP4ClientEndpoint(reactor, host, http_port)
        wrapper = SOCKSWrapper(reactor, 'localhost', socks_port, endpoint,
                               self.times)
        url = 'http://%s:%d/urandom/%d' % (host, http_port, file_size, )
        factory = client.HTTPClientFactory(url, timeout=request_timeout)
        factory.protocol = MeasuringHTTPPageGetter
        factory.deferred.addCallbacks(self._request_finished)
        deferred = wrapper.connect(factory)  # TODO use timeout, and use
                                             # remaining timeout for HTTP
                                             # request
        deferred.addCallbacks(self._request_finished)

    def _request_finished(self, ignored):
        log.msg(self.times)


class PerfdWebClient(object):

    def __init__(self, reactor, server_config, client_config):
        self._reactor = reactor
        self._public_host = server_config['public-host']
        self._http_port = server_config['http-port']
        self._source = client_config['source']
        self._launch_tor = client_config['launch-tor']
        self._tor_binary = client_config['tor-binary']
        self._start_delay = client_config['start-delay']
        self._request_delay = client_config['request-delay']
        self._socks_port = client_config['socks-port']
        self._control_port = client_config['control-port']
        self._file_size = client_config['file-size']
        self._request_timeout = client_config['request-timeout']

    def _create_config(self, proto):
        """Of course, we could use @inlineCallbacks so there are fewer tiny callbacks"""
        config = txtorcon.TorConfig(proto)
        config.post_bootstrap.addCallback(self._set_config).addErrback(self._error)

    def _set_config(self, config):
        config.SocksPort = [self._socks_port]
        config.ControlPort = [self._control_port]
        d = config.save()
        d.addCallback(self._launched, config).addErrback(self._error)

    def launch_tor(self):
        # TODO How would we not launch, but only connect to Tor?
        if True:
            ## meejah: to connect to a running system Tor, presuming
            ## default ports, simply do the following. If build_state
            ## is True instead, then the object you get back is a
            ## TorState. Below, we get back a TorControlProtocol
            ## instance (same as launch_tor())
            d = txtorcon.build_tor_connection(endpoints.TCP4ClientEndpoint(reactor, "localhost", 9051),
                                              build_state=False)
            d.addCallback(self._create_config).addErrback(self._error)
            return

        config = txtorcon.TorConfig()
        config.SocksPort = self._socks_port
        config.ControlPort = self._control_port
        updates = functools.partial(self._updates,
                                    'TOR-%d' % self._socks_port)
        d = txtorcon.launch_tor(config, self._reactor,
                                tor_binary=self._tor_binary,
                                progress_updates=updates)
        d.addCallback(self._launched, config).addErrback(self._error)

    def _launched(self, config, slave):
        print "Successfully launched Tor process!", config, slave
        self._launched_tor = slave.protocol
        self._complete(slave.protocol)

    def _error(self, fail):
        sys.stderr.write(fail.getBriefTraceback())
        return fail

    def _updates(self, tor, prog, tag, summary):
        log.msg('%s: %d%%: %s' % (tor, prog, summary))

    def _complete(self, protocol):
        log.msg('Connected to Tor version %s' % protocol.version)
        log.msg("Launching periodic requests every %f seconds" % self._request_delay)
        if False:
            # TODO Placeholder for hidden service support
            self.service_requestor = task.LoopingCall(PerfdWebRequest, self.config.HiddenServices[0].hostname,
                                                      self._http_port, self._socks_port, self._file_size, self._request_timeout)
            self.service_requestor.start(self._request_delay)

        else:
            self.service_requestor = task.LoopingCall(PerfdWebRequest, self._public_host,
                                                      self._http_port, self._socks_port, self._file_size, self._request_timeout)
            # TODO Add start delay
            self.service_requestor.start(self._request_delay)


class TorPerfdService(service.Service):
    implements(service.IService)

    def __init__(self, reactor, server_config, client_configs):
        self._reactor = reactor
        self._server_config = server_config
        self._client_configs = client_configs
        self.started_clients = []

    def privilegedStartService(self):
        service.Service.privilegedStartService(self)

        self.web_endpoint = endpoints.TCP4ServerEndpoint(self._reactor,
                            self._server_config['http-port'])
        root = resource.Resource()
        root.putChild('urandom', UrandomResourceDispatcher())
        self.web_endpoint.listen(server.Site(root))

    def startService(self):
        service.Service.startService(self)
        if self._server_config['debug-txtorcon']:
            txtorcon.log.debug_logging()
        for client_config in self._client_configs:
            client = PerfdWebClient(self._reactor, self._server_config, client_config)
            client.launch_tor()
            self.started_clients.append(client)


class TorPerfdPlugin(object):
    implements(IPlugin, service.IServiceMaker)

    tapname = 'torperfd'
    description = 'Measure the performance of the Tor network.'
    options = Options

    def makeService(self, options):
        return TorPerfdService(reactor, server_config, client_configs)

serviceMaker = TorPerfdPlugin()

if __name__ == '__main__':
    print 'Please use "twistd -n torperfd" to launch perfd.py for debugging, or use the "perfd" shell script'

class TestLaunch(unittest.TestCase):

    def test_launch(self):
        pass

