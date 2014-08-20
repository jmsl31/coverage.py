"""Raw data collector for Coverage."""

import collections, os, sys, threading

try:
    # Use the C extension code when we can, for speed.
    from coverage.tracer import CTracer         # pylint: disable=F0401,E0611
except ImportError:
    # Couldn't import the C extension, maybe it isn't built.
    if os.getenv('COVERAGE_TEST_TRACER') == 'c':
        # During testing, we use the COVERAGE_TEST_TRACER env var to indicate
        # that we've fiddled with the environment to test this fallback code.
        # If we thought we had a C tracer, but couldn't import it, then exit
        # quickly and clearly instead of dribbling confusing errors. I'm using
        # sys.exit here instead of an exception because an exception here
        # causes all sorts of other noise in unittest.
        sys.stderr.write(
            "*** COVERAGE_TEST_TRACER is 'c' but can't import CTracer!\n"
            )
        sys.exit(1)
    CTracer = None


class PyTracer(object):
    """Python implementation of the raw data tracer."""

    # Because of poor implementations of trace-function-manipulating tools,
    # the Python trace function must be kept very simple.  In particular, there
    # must be only one function ever set as the trace function, both through
    # sys.settrace, and as the return value from the trace function.  Put
    # another way, the trace function must always return itself.  It cannot
    # swap in other functions, or return None to avoid tracing a particular
    # frame.
    #
    # The trace manipulator that introduced this restriction is DecoratorTools,
    # which sets a trace function, and then later restores the pre-existing one
    # by calling sys.settrace with a function it found in the current frame.
    #
    # Systems that use DecoratorTools (or similar trace manipulations) must use
    # PyTracer to get accurate results.  The command-line --timid argument is
    # used to force the use of this tracer.

    def __init__(self):
        # Attributes set from the collector:
        self.data = None
        self.arcs = False
        self.should_trace = None
        self.should_trace_cache = None
        self.warn = None
        self.plugins = None

        self.plugin = None
        self.cur_tracename = None   # TODO: This is only maintained for the if0 debugging output. Get rid of it eventually.
        self.cur_file_data = None
        self.last_line = 0
        self.data_stack = []
        self.data_stacks = collections.defaultdict(list)
        self.last_exc_back = None
        self.last_exc_firstlineno = 0
        self.thread = None
        self.stopped = False
        self.coroutine_id_func = None
        self.last_coroutine = None

    def _trace(self, frame, event, arg_unused):
        """The trace function passed to sys.settrace."""

        if self.stopped:
            return

        if 0:
            # A lot of debugging to try to understand why gevent isn't right.
            import os.path, pprint
            def short_ident(ident):
                return "{}:{:06X}".format(ident.__class__.__name__, id(ident) & 0xFFFFFF)

            ident = None
            if self.coroutine_id_func:
                ident = short_ident(self.coroutine_id_func())
            sys.stdout.write("trace event: %s %s %r @%d\n" % (
                event, ident, frame.f_code.co_filename, frame.f_lineno
            ))
            pprint.pprint(
                dict(
                    (
                        short_ident(ident),
                        [
                            (os.path.basename(tn or ""), sorted((cfd or {}).keys()), ll)
                            for ex, tn, cfd, ll in data_stacks
                        ]
                    )
                    for ident, data_stacks in self.data_stacks.items()
                )
                , width=250)
            pprint.pprint(sorted((self.cur_file_data or {}).keys()), width=250)
            print("TRYING: {}".format(sorted(next((v for k,v in self.data.items() if k.endswith("try_it.py")), {}).keys())))

        if self.last_exc_back:
            if frame == self.last_exc_back:
                # Someone forgot a return event.
                if self.arcs and self.cur_file_data:
                    pair = (self.last_line, -self.last_exc_firstlineno)
                    self.cur_file_data[pair] = None
                if self.coroutine_id_func:
                    self.data_stack = self.data_stacks[self.coroutine_id_func()]
                self.handler, _, self.cur_file_data, self.last_line = self.data_stack.pop()
            self.last_exc_back = None

        if event == 'call':
            # Entering a new function context.  Decide if we should trace
            # in this file.
            if self.coroutine_id_func:
                self.data_stack = self.data_stacks[self.coroutine_id_func()]
                self.last_coroutine = self.coroutine_id_func()
            self.data_stack.append((self.plugin, self.cur_tracename, self.cur_file_data, self.last_line))
            filename = frame.f_code.co_filename
            disp = self.should_trace_cache.get(filename)
            if disp is None:
                disp = self.should_trace(filename, frame)
                self.should_trace_cache[filename] = disp
            #print("called, stack is %d deep, tracename is %r" % (
            #               len(self.data_stack), tracename))
            tracename = disp.filename
            if tracename and disp.plugin:
                tracename = disp.plugin.file_name(frame)
            if tracename:
                if tracename not in self.data:
                    self.data[tracename] = {}
                    if disp.plugin:
                        self.plugins[tracename] = disp.plugin.__name__
                self.cur_tracename = tracename
                self.cur_file_data = self.data[tracename]
                self.plugin = disp.plugin
            else:
                self.cur_file_data = None
            # Set the last_line to -1 because the next arc will be entering a
            # code block, indicated by (-1, n).
            self.last_line = -1
        elif event == 'line':
            # Record an executed line.
            if 0 and self.coroutine_id_func:
                this_coroutine = self.coroutine_id_func()
                if self.last_coroutine != this_coroutine:
                    print("mismatch: {0} != {1}".format(self.last_coroutine, this_coroutine))
            if self.plugin:
                lineno_from, lineno_to = self.plugin.line_number_range(frame)
            else:
                lineno_from, lineno_to = frame.f_lineno, frame.f_lineno
            if lineno_from != -1:
                if self.cur_file_data is not None:
                    if self.arcs:
                        #print("lin", self.last_line, frame.f_lineno)
                        self.cur_file_data[(self.last_line, lineno_from)] = None
                    else:
                        #print("lin", frame.f_lineno)
                        for lineno in range(lineno_from, lineno_to+1):
                            self.cur_file_data[lineno] = None
                self.last_line = lineno_to
        elif event == 'return':
            if self.arcs and self.cur_file_data:
                first = frame.f_code.co_firstlineno
                self.cur_file_data[(self.last_line, -first)] = None
            # Leaving this function, pop the filename stack.
            if self.coroutine_id_func:
                self.data_stack = self.data_stacks[self.coroutine_id_func()]
                self.last_coroutine = self.coroutine_id_func()
            self.plugin, _, self.cur_file_data, self.last_line = self.data_stack.pop()
            #print("returned, stack is %d deep" % (len(self.data_stack)))
        elif event == 'exception':
            #print("exc", self.last_line, frame.f_lineno)
            self.last_exc_back = frame.f_back
            self.last_exc_firstlineno = frame.f_code.co_firstlineno
        return self._trace

    def start(self):
        """Start this Tracer.

        Return a Python function suitable for use with sys.settrace().

        """
        self.thread = threading.currentThread()
        sys.settrace(self._trace)
        return self._trace

    def stop(self):
        """Stop this Tracer."""
        self.stopped = True
        if self.thread != threading.currentThread():
            # Called on a different thread than started us: we can't unhook
            # ourseves, but we've set the flag that we should stop, so we won't
            # do any more tracing.
            return

        if hasattr(sys, "gettrace") and self.warn:
            if sys.gettrace() != self._trace:
                msg = "Trace function changed, measurement is likely wrong: %r"
                self.warn(msg % (sys.gettrace(),))
        #print("Stopping tracer on %s" % threading.current_thread().ident)
        sys.settrace(None)

    def get_stats(self):
        """Return a dictionary of statistics, or None."""
        return None


class Collector(object):
    """Collects trace data.

    Creates a Tracer object for each thread, since they track stack
    information.  Each Tracer points to the same shared data, contributing
    traced data points.

    When the Collector is started, it creates a Tracer for the current thread,
    and installs a function to create Tracers for each new thread started.
    When the Collector is stopped, all active Tracers are stopped.

    Threads started while the Collector is stopped will never have Tracers
    associated with them.

    """

    # The stack of active Collectors.  Collectors are added here when started,
    # and popped when stopped.  Collectors on the stack are paused when not
    # the top, and resumed when they become the top again.
    _collectors = []

    def __init__(self, should_trace, timid, branch, warn, coroutine):
        """Create a collector.

        `should_trace` is a function, taking a filename, and returning a
        canonicalized filename, or None depending on whether the file should
        be traced or not.

        If `timid` is true, then a slower simpler trace function will be
        used.  This is important for some environments where manipulation of
        tracing functions make the faster more sophisticated trace function not
        operate properly.

        If `branch` is true, then branches will be measured.  This involves
        collecting data on which statements followed each other (arcs).  Use
        `get_arc_data` to get the arc data.

        `warn` is a warning function, taking a single string message argument,
        to be used if a warning needs to be issued.

        """
        self.should_trace = should_trace
        self.warn = warn
        self.branch = branch
        if coroutine == "greenlet":
            import greenlet
            self.coroutine_id_func = greenlet.getcurrent
        elif coroutine == "eventlet":
            import eventlet.greenthread
            self.coroutine_id_func = eventlet.greenthread.getcurrent
        elif coroutine == "gevent":
            import gevent
            self.coroutine_id_func = gevent.getcurrent
        else:
            self.coroutine_id_func = None
        self.reset()

        if timid:
            # Being timid: use the simple Python trace function.
            self._trace_class = PyTracer
        else:
            # Being fast: use the C Tracer if it is available, else the Python
            # trace function.
            self._trace_class = CTracer or PyTracer

    def __repr__(self):
        return "<Collector at 0x%x>" % id(self)

    def tracer_name(self):
        """Return the class name of the tracer we're using."""
        return self._trace_class.__name__

    def reset(self):
        """Clear collected data, and prepare to collect more."""
        # A dictionary mapping filenames to dicts with linenumber keys,
        # or mapping filenames to dicts with linenumber pairs as keys.
        self.data = {}

        self.plugins = {}

        # A cache of the results from should_trace, the decision about whether
        # to trace execution in a file. A dict of filename to (filename or
        # None).
        self.should_trace_cache = {}

        # Our active Tracers.
        self.tracers = []

    def _start_tracer(self):
        """Start a new Tracer object, and store it in self.tracers."""
        tracer = self._trace_class()
        tracer.data = self.data
        tracer.arcs = self.branch
        tracer.should_trace = self.should_trace
        tracer.should_trace_cache = self.should_trace_cache
        tracer.warn = self.warn
        if hasattr(tracer, 'coroutine_id_func'):
            tracer.coroutine_id_func = self.coroutine_id_func
        if hasattr(tracer, 'plugins'):
            tracer.plugins = self.plugins
        fn = tracer.start()
        self.tracers.append(tracer)
        return fn

    # The trace function has to be set individually on each thread before
    # execution begins.  Ironically, the only support the threading module has
    # for running code before the thread main is the tracing function.  So we
    # install this as a trace function, and the first time it's called, it does
    # the real trace installation.

    def _installation_trace(self, frame_unused, event_unused, arg_unused):
        """Called on new threads, installs the real tracer."""
        # Remove ourselves as the trace function
        sys.settrace(None)
        # Install the real tracer.
        fn = self._start_tracer()
        # Invoke the real trace function with the current event, to be sure
        # not to lose an event.
        if fn:
            fn = fn(frame_unused, event_unused, arg_unused)
        # Return the new trace function to continue tracing in this scope.
        return fn

    def start(self):
        """Start collecting trace information."""
        if self._collectors:
            self._collectors[-1].pause()
        self._collectors.append(self)
        #print("Started: %r" % self._collectors, file=sys.stderr)

        # Check to see whether we had a fullcoverage tracer installed.
        traces0 = []
        if hasattr(sys, "gettrace"):
            fn0 = sys.gettrace()
            if fn0:
                tracer0 = getattr(fn0, '__self__', None)
                if tracer0:
                    traces0 = getattr(tracer0, 'traces', [])

        # Install the tracer on this thread.
        fn = self._start_tracer()

        for args in traces0:
            (frame, event, arg), lineno = args
            try:
                fn(frame, event, arg, lineno=lineno)
            except TypeError:
                raise Exception(
                    "fullcoverage must be run with the C trace function."
                )

        # Install our installation tracer in threading, to jump start other
        # threads.
        threading.settrace(self._installation_trace)

    def stop(self):
        """Stop collecting trace information."""
        #print >>sys.stderr, "Stopping: %r" % self._collectors
        assert self._collectors
        assert self._collectors[-1] is self

        self.pause()
        self.tracers = []

        # Remove this Collector from the stack, and resume the one underneath
        # (if any).
        self._collectors.pop()
        if self._collectors:
            self._collectors[-1].resume()

    def pause(self):
        """Pause tracing, but be prepared to `resume`."""
        for tracer in self.tracers:
            tracer.stop()
            stats = tracer.get_stats()
            if stats:
                print("\nCoverage.py tracer stats:")
                for k in sorted(stats.keys()):
                    print("%16s: %s" % (k, stats[k]))
        threading.settrace(None)

    def resume(self):
        """Resume tracing after a `pause`."""
        for tracer in self.tracers:
            tracer.start()
        threading.settrace(self._installation_trace)

    def get_line_data(self):
        """Return the line data collected.

        Data is { filename: { lineno: None, ...}, ...}

        """
        if self.branch:
            # If we were measuring branches, then we have to re-build the dict
            # to show line data.
            line_data = {}
            for f, arcs in self.data.items():
                line_data[f] = dict((l1, None) for l1, _ in arcs.keys() if l1)
            return line_data
        else:
            return self.data

    def get_arc_data(self):
        """Return the arc data collected.

        Data is { filename: { (l1, l2): None, ...}, ...}

        Note that no data is collected or returned if the Collector wasn't
        created with `branch` true.

        """
        if self.branch:
            return self.data
        else:
            return {}

    def get_plugin_data(self):
        return self.plugins
