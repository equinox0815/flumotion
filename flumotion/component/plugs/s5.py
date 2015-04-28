#
# sfive
#
# sfive - spreadspace streaming statistics suite is a generic
# statistic collection tool for streaming server infrastuctures.
# The system collects and stores meta data like number of views
# and throughput from a number of streaming servers and stores
# it in a global data store.
# The data acquisition is designed to be generic and extensible in
# order to support different streaming software.
# sfive also contains tools and applications to filter and visualize
# live and recorded data.
#
#
# Copyright (C) 2014 Christian Pointner <equinox@spreadspace.org>
#                    Markus Grueneis <gimpf@gimpf.org>
#
# This file is part of sfive.
#
# sfive is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3
# as published by the Free Software Foundation.
#
# sfive is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with sfive. If not, see <http://www.gnu.org/licenses/>.
#

import os

try:
    import simplejson as json
except ImportError:
    json = None

from flumotion.component.plugs import base
from flumotion.common import messages, i18n, log
from flumotion.common.poller import Poller

from twisted.internet import protocol, reactor
from socket import error as socket_error
import datetime
import time

from flumotion.common.i18n import N_
T_ = i18n.gettexter()

_DEFAULT_POLL_INTERVAL = 5 # in seconds
_RECONNECT_TIMEOUT = 2 # in seconds
_MAX_PACKET_SIZE = 8192 # in bytes

__version__ = "$Rev$"

class SFiveProto(protocol.ConnectedDatagramProtocol):

    def __init__(self, plug):
        self._plug = plug

    def stopProtocol(self):
        self._plug.debug('SFive: protocol stopped')

    def startProtocol(self):
        self._plug.debug('SFive: protocol started')
        self._plug._socketReady()

    def datagramReceived(self, data):
        self._plug.debug('SFive: received datagram: "%s" (will get ignored)', data)

    def connectionFailed(self, failure):
        self._plug.warning('SFive: connection failed: %s', failure.getErrorMessage())
        self._plug._socketError()

    def sendDatagram(self, data):
        try:
            ## TODO: twisted will drop messages if the write buffer is full.
            ##       Some batch importer work around this issue by sleeping
            ##       and trying again. For live importer the fix is not applicable
            ##       but also not as common because unlike live sources batch
            ##       importer produce a lot of data in a very short period of time.
            ##       Anyway this issue needs to be addressed!
            return self.transport.write(data)
        except socket_error as err:
            self._plug.warning('SFive: sending datagram failed: %s', err)
            self._plug._socketError()



class ComponentSFivePlug(base.ComponentPlug):
    """Class to send statistics to the spreadspace streaming statistic suite"""

    ### ComponentPlug methods

    def start(self, component):
        self.debug('SFive: plug loaded')

        self._sfivepoller = None
        self._component = component

        if not self._hasImport():
            return

        properties = self.args['properties']
        self._socket = properties['socket']
        self._hostname = properties['hostname']
        self._content_id = properties.get('content-id')
        self._format = properties.get('format')
        self._quality = properties.get('quality')
        tagstring = properties.get('tags', '')
        self._tags = [x.strip() for x in tagstring.split(',')]

        self._duration = properties.get('duration', _DEFAULT_POLL_INTERVAL)
        self._sfivepoller = Poller(self._updateSFive, self._duration, start=False)

        self._proto = None
        self._conn = None

        reactor.callLater(_RECONNECT_TIMEOUT, self._initSocket)

    def _hasImport(self):
        """Check simplejson availability"""
        if not json:
            m = messages.Warning(T_(N_(
                "Cannot import module '%s'.\n"), 'simplejson'),
                                    mid='simplejson-import-error')
            m.add(T_(N_(
                "The SFive plug for this component is disabled.")))
            self._component.addMessage(m)
            return False

        return True

    def stop(self, component):
        if self._sfivepoller:
            self._sfivepoller.stop()
        if self._conn:
            self._conn.stopListening()

    def _initSocket(self):
        self.info('SFive: trying to (re)connect to %s...', self._socket)
        self._proto = SFiveProto(self)
        self._conn = reactor.connectUNIXDatagram(self._socket, self._proto, maxPacketSize=_MAX_PACKET_SIZE)

    def _socketError(self):
        if self._sfivepoller:
            self._sfivepoller.stop()
        if self._conn:
            self._conn.stopListening()

        reactor.callLater(_RECONNECT_TIMEOUT, self._initSocket)

    def _socketReady(self):
        self.info('SFive: connection to sfive hub established')
# we are using datagram sockets for now -> must use stateless protocol
#        self._sendInit()
        if self._sfivepoller:
            # try to be aligned with current time
            # this will eventually get out of sync but for now this is good enough
            offset = self._duration - (time.time() % self._duration)
            self.info('SFive: %sZ -> will wait %0.2f seconds before starting poller (alignment)' % (datetime.datetime.utcnow().isoformat('T'), offset))
            reactor.callLater(offset, self._startPoller)

    def _startPoller(self):
        self._old_bytes_received = self._component.getBytesReceived()
        self._old_bytes_sent = self._component.getBytesSent()
        self._start_time = datetime.datetime.utcnow().replace(microsecond=0)
        self._sfivepoller.start()
        self.info('SFive: poller started at %sZ' % (self._start_time.isoformat('T')))

    def _updateSFive(self):
        client_count = self._component.getClients()
        bytes_received = self._component.getBytesReceived()
        bytes_sent = self._component.getBytesSent()

        bytes_received_diff = bytes_received - self._old_bytes_received
        self._old_bytes_received = bytes_received
        bytes_sent_diff = bytes_sent - self._old_bytes_sent
        self._old_bytes_sent = bytes_sent

        self._sendDatasetFull(self._start_time, self._duration, client_count, bytes_sent_diff, bytes_received_diff)
        self._start_time = datetime.datetime.utcnow().replace(microsecond=0)


    def _sendDatasetFull(self, timestamp, duration, client_count, bytes_sent, bytes_received):
        data = { "version": 1, "hostname": self._hostname,
                 "streamer-id": {
                     "content-id": self._content_id,
                     "format": self._format,
                     "quality": self._quality
                 },
                 "tags": self._tags,
                 "start-time": timestamp.isoformat('T') + 'Z',
                 "duration-ms": duration * 1000,
                 "data": {
                   "client-count": client_count,
                   "bytes-received": bytes_received,
                   "bytes-sent": bytes_sent
                 }
               }
        self._proto.sendDatagram('%s\n' % (json.dumps(data)))

    def _sendInit(self):
        initdata = { "version": 1, "hostname": self._hostname,
                     "streamer-id": { "content-id": self._content_id, "format": self._format, "quality": self._quality },
                     "tags": self._tags }
        self._proto.sendDatagram('%s\n' % (json.dumps(initdata)))

    def _sendDataset(self, timestamp, duration, client_count, bytes_sent, bytes_received):
        data = { "start-time": timestamp.isoformat('T') + 'Z',
                 "duration-ms": duration * 1000,
                 "data": {
                   "client-count": client_count,
                   "bytes-received": bytes_received,
                   "bytes-sent": bytes_sent
                 }
               }
        self._proto.sendDatagram('%s\n' % (json.dumps(data)))
