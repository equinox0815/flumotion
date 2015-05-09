# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4

# Flumotion - a streaming media server
# Copyright (C) 2004,2005,2006,2007,2008,2009 Fluendo, S.L.
# Copyright (C) 2010,2011 Flumotion Services, S.A.
# All rights reserved.
#
# This file may be distributed and/or modified under the terms of
# the GNU Lesser General Public License version 2.1 as published by
# the Free Software Foundation.
# This file is distributed without any warranty; without even the implied
# warranty of merchantability or fitness for a particular purpose.
# See "LICENSE.LGPL" in the source distribution for more information.
#
# Headers in this file shall remain intact.

"""
main for flumotion-checkmk
"""

import os
import sys

from twisted.internet import reactor, defer

from flumotion.common import errors, planet
from flumotion.admin import admin
from flumotion.common.connection import parsePBConnectionInfo


__version__ = "$Rev$"

FLUMOTION_CONFIG_D='/etc/flumotion'
CHECK_MK_CONFIG_D='/etc/check_mk'

class CheckMkPlanet():
    adminModel = None      # AdminModel connected to the manager

    def __init__(self, handle, cfg):
        self._handle = handle
        self._host = cfg.get('host', 'localhost')
        self._port = int(cfg.get('port', '7531'))
        self._use_ssl = cfg.get('transport', 'ssl') == 'ssl'
        self._user = cfg.get('username', 'admin')
        self._passwd = cfg.get('password', '')
        self._output = ''

    def start(self):
        self._output += '[planet/%s]\n' % self._handle
        self._deferred = defer.Deferred()
        self.connect()
        return self._deferred

    def connect(self):
        connection = parsePBConnectionInfo(self._host, self._user, self._passwd, self._port, self._use_ssl)

        # platform-3/trunk compatibility stuff to guard against
        # gratuitous changes
        try:
            # platform-3
            self.adminModel = admin.AdminModel(connection.authenticator)
        except TypeError:
            # trunk
            self.adminModel = admin.AdminModel()

        if hasattr(self.adminModel, 'connectToHost'):
            # platform-3
            d = self.adminModel.connectToHost(connection.host,
                connection.port, not connection.use_ssl)
        else:
            d = self.adminModel.connectToManager(connection)

        d.addCallback(self._connectedCb)
        d.addErrback(self._connectedEb)


    def _connectedCb(self, result):
        d = self.planetState(result)
        d.addBoth(self._doneCB)


    def _connectedEb(self, f):
        if f.check(errors.ConnectionFailedError):
            self._output += 'state = unknown, unable to connect to manager.\n'

        if f.check(errors.ConnectionRefusedError):
            self._output += 'state = unknown, manager refused connection.\n'

        self._deferred.callback(self._output)


    def _doneCB(self, result):
        self._deferred.callback(self._output)


    def planetState(self, result):
        d = self.adminModel.callRemote('getPlanetState')

        def printComponentMood(p, f, c):
            self._output += '[planet/%s:%s/%s]\n' % (self._handle, f.get('name'), c.get('name'))
            ignored_keys = [ 'parent', 'messages', 'pid', 'lastKnownPid' ]
            for k in c.keys():
                v = c.get(k)
                if k == 'mood' or k == 'moodPending':
                    try:
                        v = planet.moods.get(v).name
                    except KeyError:
                        v = None

                if k not in ignored_keys:
                    self._output += '%s = %s\n' % (k, v)


        def gotPlanetStateCb(planet):
            self._output += 'state = up, connected to manager.\n'
            self._output += 'name = %s\n' % planet.get('name')
            self._output += 'version = %s\n' % planet.get('version')

            for c in planet.get('atmosphere').get('components'):
                printComponentMood(planet, 'atomospere', c)

            for f in planet.get('flows'):
                for c in f.get('components'):
                    printComponentMood(planet, f, c)

        def gotPlanetStateEb(ign):
            self._output += 'state = error, unable to fetch planet state.\n'

        d.addCallback(gotPlanetStateCb)
        d.addErrback(gotPlanetStateEb)
        return d


def main(args):
    cfg = {}
    cfg['planets'] = {}

    sys.stdout.write("<<<flumotion>>>\n")
    try:
        import ConfigParser

        config = ConfigParser.RawConfigParser()
        config_file = os.path.join(CHECK_MK_CONFIG_D, 'flumotion.cfg')
        config.read(config_file)

        dir = os.path.join(FLUMOTION_CONFIG_D, 'managers')
        planets = [ p for p in os.listdir(dir) if os.path.isdir(os.path.join(dir,p)) and os.path.isfile(os.path.join(dir,p,'planet.xml')) ]
        for p in planets:
            section = 'planet:%s' %p
            if config.has_section(section):
                cfg['planets'][p] = {}
                for k, v in config.items(section):
                    cfg['planets'][p][k] = v
            else:
                sys.stdout.write('[planet/%s]\n' % p)
                sys.stdout.write('state = unknown, Please add connection information to %s\n' % config_file)

    except Exception, e:
        sys.stderr.write('Error while reading config file: %s\n' % e.message)
        return

    if not cfg['planets']:
        ## no planets to check
        return

    try:
        def printOutput(output):
            sys.stdout.write(output)

        def finish(ign):
            reactor.stop()

        planet_list = []
        for name, cfg in cfg['planets'].items():
            p = CheckMkPlanet(name, cfg)
            d = p.start()
            d.addCallback(printOutput)
            planet_list.append(d)

        planets = defer.DeferredList(planet_list)
        planets.addBoth(finish)
        reactor.run()

    except Exception, e:
        sys.stderr.write('%s\n' % e.message)
