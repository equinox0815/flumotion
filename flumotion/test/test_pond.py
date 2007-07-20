# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
#
# Flumotion - a streaming media server
# Copyright (C) 2004,2005,2006,2007 Fluendo, S.L. (www.fluendo.com).
# All rights reserved.

# This file may be distributed and/or modified under the terms of
# the GNU General Public License version 2 as published by
# the Free Software Foundation.
# This file is distributed without any warranty; without even the implied
# warranty of merchantability or fitness for a particular purpose.
# See "LICENSE.GPL" in the source distribution for more information.

# Licensees having purchased or holding a valid Flumotion Advanced
# Streaming Server license may use this file in accordance with the
# Flumotion Advanced Streaming Server Commercial License Agreement.
# See "LICENSE.Flumotion" in the source distribution for more information.

# Headers in this file shall remain intact.

from twisted.trial import unittest

import common

import sys, time, os

from twisted.python import failure
from twisted.internet import defer, interfaces, reactor, gtk2reactor
from twisted.web import client, error

from flumotion.common import log, errors
from flumotion.common.planet import moods

from flumotion.test import pond

from flumotion.component.producers.pipeline.pipeline import Producer
from flumotion.component.converters.pipeline.pipeline import Converter

class PondTestCase(log.Loggable, unittest.TestCase, pond.PondUnitTestMixin):
    logCategory = 'pond-test'

class TestPondGtk2Reactorness(unittest.TestCase):
    def test_mixin_class(self):
        class TestPondUnitTestMixin(unittest.TestCase, pond.PondUnitTestMixin):
            pass
        if not isinstance(sys.modules['twisted.internet.reactor'],
                          gtk2reactor.Gtk2Reactor):
            # not running with a gtk2reactor, TestPondUnitTestMixin
            # class should have .skip attribute
            self.failUnless(hasattr(TestPondUnitTestMixin, 'skip'),
                            "PondUnitTestMixin doesn't set .skip correctly.")
        else:
            self.failIf(hasattr(TestPondUnitTestMixin, 'skip'),
                        "PondUnitTestMixin sets .skip incorrectly.")

    def test_have_gtk2reactor(self):
        if not isinstance(sys.modules['twisted.internet.reactor'],
                          gtk2reactor.Gtk2Reactor):
            # not running with a gtk2reactor, pond.HAVE_GTK2REACTOR
            # should be False
            self.failUnlessEquals(pond.HAVE_GTK2REACTOR, False)
        else:
            self.failIfEquals(pond.HAVE_GTK2REACTOR, False)

class TestComponentWrapper(unittest.TestCase):
    def test_get_unique_name(self):
        self.failIfEquals(pond.ComponentWrapper.get_unique_name(),
                          pond.ComponentWrapper.get_unique_name())


    def test_invalid_type(self):
        self.failUnlessRaises(errors.UnknownComponentError,
                              pond.ComponentWrapper, 'invalid-comp-type',
                              None)

    def test_valid_type(self):
        cw = pond.ComponentWrapper('pipeline-producer', None, name='pp')
        self.failUnlessEquals(cw.cfg,
                              {'feed': ['default'], 'name': 'pp',
                               'parent': 'default', 'clock-master': None,
                               'avatarId': '/default/pp', 'eater': {},
                               'source': [], 'plugs': {}, 'properties': {},
                               'type': 'pipeline-producer'})


    def test_simple_link(self):
        pp = pond.ComponentWrapper('pipeline-producer', None, name='pp')
        pc = pond.ComponentWrapper('pipeline-converter', None)

        pp.feed(pc)
        self.failUnlessEquals(pc.cfg['source'], ['pp:default'])
        self.failUnlessEquals(pc.cfg['eater'], {'default': ['pp:default']})

    def test_non_default_link(self):
        fwp = pond.ComponentWrapper('firewire-producer', None, name='fwp')
        pc = pond.ComponentWrapper('pipeline-converter', None, name='pc')

        # this should raise an exception - firewire-producer doesn't
        # have a default feeder
        self.failUnlessRaises(pond.PondException, fwp.feed, pc)

        fwp.feed(pc, [('video', 'default')])
        fwp.feed(pc, [('audio', 'default')])

        self.failUnlessEquals(pc.cfg['source'], ['fwp:video', 'fwp:audio'])
        self.failUnlessEquals(pc.cfg['eater'],
                              {'default': ['fwp:video', 'fwp:audio']})


    def test_instantiate_and_setup_errors(self):
        pp = pond.ComponentWrapper('pipeline-producer', None, name='pp')
        self.failUnlessRaises(TypeError, pp.instantiate) # None()!?
        from flumotion.component.producers.pipeline import pipeline
        pp = pond.ComponentWrapper('pipeline-producer', Producer, name='pp')

        pp.instantiate()
        d = pp.setup()

        # the deferred should fail (no mandatory pipeline property) -
        # stop the component in any case (to clean the reactor) and
        # passtrough the result/failure
        d.addBoth(lambda rf: (pp.stop(), rf)[1])
        return self.failUnlessFailure(d, errors.ComponentSetupHandledError)

    def test_setup_pipeline_error(self):
        from flumotion.component.producers.pipeline import pipeline
        pp = pond.ComponentWrapper('pipeline-producer', Producer,
                                   name='pp', props={'pipeline': 'fakesink'})

        pp.instantiate()
        # we're going to fail in gst - make sure the gst logger is silent
        import gst
        old_debug_level = gst.debug_get_default_threshold()
        gst.debug_set_default_threshold(gst.LEVEL_NONE)

        d = pp.setup()

        # the deferred should fail (the only pipeline element doesn't
        # have source pads) - stop the component in any case (to clean
        # the reactor) and passtrough the result/failure

        d.addBoth(lambda rf: (pp.stop(), rf)[1])
        if old_debug_level != gst.LEVEL_NONE:
            def _restore_gst_debug_level(rf):
                gst.debug_set_default_threshold(old_debug_level)
                return rf
            d.addBoth(_restore_gst_debug_level)
        return self.failUnlessFailure(d, errors.ComponentSetupHandledError)

    def test_setup_and_stop(self):
        from flumotion.component.producers.pipeline import pipeline
        pp = pond.ComponentWrapper('pipeline-producer', Producer,
                                   name='pp', props={'pipeline': 'fakesrc'})

        pp.instantiate()
        d = pp.setup()

        d.addCallback(lambda _: pp.stop())
        return d

class TestPondSetup(PondTestCase):
    def setUp(self):
        self.prod = pond.pipeline_src()
        self.cnv1 = pond.pipeline_cnv()
        self.cnv2 = pond.pipeline_cnv()
        self.components = [self.prod, self.cnv1, self.cnv2]

        self.p = pond.Pond()

    def tearDown(self):
        return defer.DeferredList([c.stop() for c in self.components])


    def test_auto_linking(self):
        # the components should be linked automatically
        # [prod:default] --> [default:cnv1:default] --> [default:cnv2]
        self.p.set_flow([self.prod, self.cnv1, self.cnv2])

        prod_feed = '%s:%s' % (self.prod.name, self.prod.cfg['feed'][0])
        self.failUnlessEquals([prod_feed], self.cnv1.cfg['source'])
        self.failUnlessEquals({'default': [prod_feed]}, self.cnv1.cfg['eater'])

        cnv1_feed = '%s:%s' % (self.cnv1.name, self.cnv1.cfg['feed'][0])
        self.failUnlessEquals([cnv1_feed], self.cnv2.cfg['source'])
        self.failUnlessEquals({'default': [cnv1_feed]}, self.cnv2.cfg['eater'])

    def test_dont_auto_link_linked(self):
        p2 = pond.pipeline_src()
        self.components.append(p2)

        p2.feed(self.cnv1)
        self.prod.auto_link = False

        # [  p2:default] --> [default:cnv1], set explicitly
        # no p2 --> prod, explicitly prohibited
        # [prod:default] --> [default:cnv2]
        self.p.set_flow([p2, self.prod, self.cnv2, self.cnv1])

        prod_feed = '%s:%s' % (p2.name, p2.cfg['feed'][0])
        self.failUnlessEquals([prod_feed], self.cnv1.cfg['source'])
        self.failUnlessEquals({'default': [prod_feed]}, self.cnv1.cfg['eater'])

        self.failUnlessEquals([], self.prod.cfg['source'])
        self.failUnlessEquals({}, self.prod.cfg['eater'])

        prod_feed = '%s:%s' % (self.prod.name, self.prod.cfg['feed'][0])
        self.failUnlessEquals([prod_feed], self.cnv2.cfg['source'])
        self.failUnlessEquals({'default': [prod_feed]}, self.cnv2.cfg['eater'])

    def test_master_clock(self):
        p2 = pond.pipeline_src()
        self.components.append(p2)

        p2.feed(self.cnv1)
        self.prod.feed(self.cnv1)
        self.cnv1.feed(self.cnv2)

        self.p.set_flow([self.prod, p2, self.cnv1, self.cnv2], auto_link=False)

        # both prod and p2 require a clock, only one should provide it
        self.failUnlessEquals(self.prod.cfg['clock-master'],
                              p2.cfg['clock-master'])
        self.failUnlessEquals(self.cnv1.cfg['clock-master'], None)
        self.failUnlessEquals(self.cnv2.cfg['clock-master'], None)

        master = self.prod
        slave = p2
        if master.cfg['clock-master'] != master.cfg['avatarId']:
            slave, master = master, slave

        # the master-clock component should provide a clock, and not
        # require an external clock source, as opposed the the slave
        self.failUnlessEquals(master.sync, None)
        self.failIfEquals(slave.sync, None)

class TestPondFlow(PondTestCase):
    def setUp(self):
        self.duration = 2.0

        prod_pp = ('videotestsrc is-live=true ! '
                   'video/x-raw-rgb,framerate=(fraction)8/1,'
                   'width=32,height=24')
        self.prod = pond.pipeline_src(prod_pp)

        self.cnv1 = pond.pipeline_cnv()
        self.cnv2 = pond.pipeline_cnv()

        self.p = pond.Pond()

    def tearDown(self):
        d = self.p.stop_flow()

        # add cleanup, otherwise components a.t.m. don't cleanup after
        # themselves too well, remove when fixed
        d.addBoth(lambda _: pond.cleanup_reactor())
        return d


    def test_setup_fail_gst_linking(self):
        p2 = pond.pipeline_src('fakesink') # this just can't work!
        c2 = pond.pipeline_cnv('fakesink') # and neither can this!

        # we're going to fail in gst - make sure the gst logger is silent
        import gst
        old_debug_level = gst.debug_get_default_threshold()
        gst.debug_set_default_threshold(gst.LEVEL_NONE)

        self.p.set_flow([p2, c2, self.cnv1])
        d = self.p.start_flow()

        if old_debug_level != gst.LEVEL_NONE:
            def _restore_gst_debug_level(rf):
                gst.debug_set_default_threshold(old_debug_level)
                return rf
            d.addBoth(_restore_gst_debug_level)
        return self.failUnlessFailure(d, errors.ComponentSetupHandledError)

    def test_setup_started_and_happy(self):
        self.p.set_flow([self.prod, self.cnv1, self.cnv2])
        d = self.p.start_flow()
        def check_happy(_):
            for c in (self.prod, self.cnv1, self.cnv2):
                self.assertEquals(moods.get(c.comp.getMood()), moods.happy)
            return _
        d.addCallback(check_happy)
        return d

    def test_run_fail_gst_linking(self):
        p2 = pond.pipeline_src('fakesink') # this just can't work!
        c2 = pond.pipeline_cnv('fakesink') # and neither can this!

        # we're going to fail in gst - make sure the gst logger is silent
        import gst
        old_debug_level = gst.debug_get_default_threshold()
        gst.debug_set_default_threshold(gst.LEVEL_NONE)

        self.p.set_flow([p2, c2, self.cnv1])
        d = self.p.run_flow(self.duration)

        if old_debug_level != gst.LEVEL_NONE:
            def _restore_gst_debug_level(rf):
                gst.debug_set_default_threshold(old_debug_level)
                return rf
            d.addBoth(_restore_gst_debug_level)
        return self.failUnlessFailure(d, errors.ComponentSetupHandledError)

    def test_run_start_timeout(self):
        start_delay_time = 5.0
        self.p.guard_timeout = 2.0
        class LingeringCompWrapper(pond.ComponentWrapper):
            def start(self, *a, **kw):
                d = pond.ComponentWrapper.start(self, *a, **kw)
                def delay_start(result):
                    dd = defer.Deferred()
                    reactor.callLater(start_delay_time, dd.callback, result)
                    return dd
                d.addCallback(delay_start)
                return d
        c2 = LingeringCompWrapper('pipeline-converter', Converter,
                                  props={'pipeline': 'identity'})
        self.p.set_flow([self.prod, c2])
        d = self.p.run_flow(self.duration)
        return self.failUnlessFailure(d, pond.StartTimeout)

    def test_run_tasks_chained_and_fired(self):
        self.tasks_fired = []
        self.tasks = []
        num_tasks = 5
        def tasks_started(result, index):
            self.tasks_fired[index] = True
            return result
        def tasks_check(result):
            self.failIfIn(False, self.tasks_fired)
            self.failIfIn(False, self.tasks_fired)
            return result
        for i in range(num_tasks):
            self.tasks_fired.append(False)
            d = defer.Deferred()
            self.tasks.append(d)
            d.addCallback(tasks_started, i)

        self.p.set_flow([self.prod, self.cnv1, self.cnv2])
        d = self.p.run_flow(self.duration, tasks=self.tasks)
        d.addCallback(tasks_check)

        return d

    def test_run_started_happy_and_running_and_stopping(self):
        self.time_tolerance = 0.5 # should suffice, no?
        self.timer = 0.0

        self.p.set_flow([self.prod, self.cnv1, self.cnv2])

        def check_happy(_):
            for c in (self.prod, self.cnv1, self.cnv2):
                self.assertEquals(moods.get(c.comp.getMood()), moods.happy)
            return _
        def timer_start(result):
            self.timer = time.time()
            return result
        def timer_check(result):
            time_difference = abs(time.time() - self.timer - self.duration)
            self.failUnless(time_difference < self.time_tolerance,
                            "Time difference too big: %r" % time_difference)
            return result

        task_d = defer.Deferred()
        task_d.addCallback(check_happy)
        task_d.addCallback(timer_start)

        d = self.p.run_flow(self.duration, tasks=[task_d])
        d.addCallback(timer_check)

        return d

    def test_run_tasks_timeout(self):
        self.p.set_flow([self.prod, self.cnv1, self.cnv2])
        self.p.guard_timeout = 4.0

        def make_eternal_deferred(_):
            # never going to fire this one...
            eternal_d = defer.Deferred()
            return eternal_d
        task_d = defer.Deferred()
        task_d.addCallback(make_eternal_deferred)

        d = self.p.run_flow(self.duration, tasks=[task_d])

        return self.failUnlessFailure(d, pond.FlowTimeout)

    def test_run_stop_timeout(self):
        stop_delay_time = 6.0
        self.p.guard_timeout = 4.0
        class DelayingCompWrapper(pond.ComponentWrapper):
            do_delay = True
            def stop(self, *a, **kw):
                d = pond.ComponentWrapper.stop(self, *a, **kw)
                def delay_stop(result):
                    if self.do_delay:
                        self.do_delay = False
                        dd = defer.Deferred()
                        reactor.callLater(stop_delay_time, dd.callback, result)
                        return dd
                    return result
                d.addCallback(delay_stop)
                return d
        c2 = DelayingCompWrapper('pipeline-converter', Converter,
                                 props={'pipeline': 'identity'})
        self.p.set_flow([self.prod, c2])
        d = self.p.run_flow(self.duration)
        return self.failUnlessFailure(d, pond.StopTimeout)

    def test_run_started_then_fails(self):
        self.p.set_flow([self.prod, self.cnv1, self.cnv2])
        wrench_timeout = 0.5

        class CustomWrenchException(Exception):
            pass
        def insert_wrenches_into_cogs(_):
            def insert_wrench(c):
                raise CustomWrenchException("Wasn't that loose?")
            d = defer.Deferred()
            d.addCallback(insert_wrench)
            reactor.callLater(wrench_timeout, d.callback, self.cnv1)
            return d
        task_d = defer.Deferred()
        task_d.addCallback(insert_wrenches_into_cogs)

        d = self.p.run_flow(self.duration, tasks=[task_d])
        return self.failUnlessFailure(d, CustomWrenchException)

    def test_run_started_then_flow_and_stop_fail(self):
        flow_error_timeout = 0.5

        class CustomFlowException(Exception):
            pass
        class CustomStopException(Exception):
            pass

        class BrokenCompWrapper(pond.ComponentWrapper):
            do_break = True
            def stop(self, *a, **kw):
                d = pond.ComponentWrapper.stop(self, *a, **kw)
                def delay_stop(result):
                    # breaking once should be enough
                    if self.do_break:
                        self.do_break = False
                        raise CustomStopException()
                d.addCallback(delay_stop)
                return d
        c2 = BrokenCompWrapper('pipeline-converter', Converter,
                               props={'pipeline': 'identity'})
        self.p.set_flow([self.prod, c2])

        class CustomFlowException(Exception):
            pass
        def insert_flow_errors(_):
            def insert_error(_ignore):
                raise CustomFlowException("This pond is too small!")
            d = defer.Deferred()
            d.addCallback(insert_error)
            reactor.callLater(flow_error_timeout, d.callback, None)
            return d
        task_d = defer.Deferred()
        task_d.addCallback(insert_flow_errors)
        d = self.p.run_flow(self.duration, tasks=[task_d])
        return self.failUnlessFailure(d, CustomFlowException)
