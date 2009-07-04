"""Base test case class for coverage testing."""

import imp, os, random, shutil, sys, tempfile, textwrap, unittest
from cStringIO import StringIO

# Python version compatibility
try:
    set       # new in 2.4
except NameError:
    # pylint: disable-msg=W0622
    # (Redefining built-in 'set')
    from sets import Set as set

import coverage


class Tee(object):
    """A file-like that writes to all the file-likes it has."""

    def __init__(self, *files):
        """Make a Tee that writes to all the files in `files.`"""
        self.files = files
        
    def write(self, data):
        """Write `data` to all the files."""
        for f in self.files:
            f.write(data)


class CoverageTest(unittest.TestCase):
    """A base class for Coverage test cases."""

    def setUp(self):
        # Create a temporary directory.
        self.noise = str(random.random())[2:]
        self.temp_root = os.path.join(tempfile.gettempdir(), 'test_coverage')
        self.temp_dir = os.path.join(self.temp_root, self.noise)
        os.makedirs(self.temp_dir)
        self.old_dir = os.getcwd()
        os.chdir(self.temp_dir)

        # Modules should be importable from this temp directory.
        self.old_syspath = sys.path[:]
        sys.path.insert(0, '')

        # Keep a counter to make every call to checkCoverage unique.
        self.n = 0

        # Use a Tee to capture stdout.
        self.old_stdout = sys.stdout
        self.captured_stdout = StringIO()
        sys.stdout = Tee(sys.stdout, self.captured_stdout)
        
    def tearDown(self):
        # Restore the original sys.path
        sys.path = self.old_syspath
        
        # Get rid of the temporary directory.
        os.chdir(self.old_dir)
        shutil.rmtree(self.temp_root)
        
        # Restore stdout.
        sys.stdout = self.old_stdout

    def stdout(self):
        """Return the data written to stdout during the test."""
        return self.captured_stdout.getvalue()

    def makeFile(self, filename, text):
        """Create a temp file.
        
        `filename` is the file name, and `text` is the content.
        
        """
        text = textwrap.dedent(text)
        
        # Create the file.
        f = open(filename, 'w')
        f.write(text)
        f.close()

    def importModule(self, modname):
        """Import the module named modname, and return the module object."""
        modfile = modname + '.py'
        f = open(modfile, 'r')
        
        for suff in imp.get_suffixes():
            if suff[0] == '.py':
                break
        try:
            # pylint: disable-msg=W0631
            # (Using possibly undefined loop variable 'suff')
            mod = imp.load_module(modname, f, modfile, suff)
        finally:
            f.close()
        return mod

    def getModuleName(self):
        """Return the module name to use for this test run."""
        # We append self.n because otherwise two calls in one test will use the
        # same filename and whether the test works or not depends on the
        # timestamps in the .pyc file, so it becomes random whether the second
        # call will use the compiled version of the first call's code or not!
        modname = 'coverage_test_' + self.noise + str(self.n)
        self.n += 1
        return modname
    
    def checkCoverage(self, text, lines, missing="", excludes=None, report=""):
        """Check the coverage measurement of `text`.
        
        The source `text` is run and measured.  `lines` are the line numbers
        that are executable, `missing` are the lines not executed, `excludes`
        are regexes to match against for excluding lines, and `report` is
        the text of the measurement report.
        
        """
        # We write the code into a file so that we can import it.
        # Coverage wants to deal with things as modules with file names.
        modname = self.getModuleName()
        
        self.makeFile(modname+".py", text)

        # Start up Coverage.
        cov = coverage.coverage()
        cov.erase()
        for exc in excludes or []:
            cov.exclude(exc)
        cov.start()

        # Import the python file, executing it.
        mod = self.importModule(modname)
        
        # Stop Coverage.
        cov.stop()

        # Clean up our side effects
        del sys.modules[modname]

        # Get the analysis results, and check that they are right.
        _, clines, _, cmissing = cov.analysis(mod)
        if lines is not None:
            if type(lines[0]) == type(1):
                self.assertEqual(clines, lines)
            else:
                for line_list in lines:
                    if clines == line_list:
                        break
                else:
                    self.fail("None of the lines choices matched %r" % clines)
        if missing is not None:
            if type(missing) == type(""):
                self.assertEqual(cmissing, missing)
            else:
                for missing_list in missing:
                    if cmissing == missing_list:
                        break
                else:
                    self.fail(
                        "None of the missing choices matched %r" % cmissing
                        )

        if report:
            frep = StringIO()
            cov.report(mod, file=frep)
            rep = " ".join(frep.getvalue().split("\n")[2].split()[1:])
            self.assertEqual(report, rep)

    def assertRaisesMsg(self, excClass, msg, callableObj, *args, **kwargs):
        """ Just like unittest.TestCase.assertRaises,
            but checks that the message is right too.
        """
        try:
            callableObj(*args, **kwargs)
        except excClass, exc:
            excMsg = str(exc)
            if not msg:
                # No message provided: it passes.
                return  #pragma: no cover
            elif excMsg == msg:
                # Message provided, and we got the right message: it passes.
                return
            else:   #pragma: no cover
                # Message provided, and it didn't match: fail!
                raise self.failureException(
                    "Right exception, wrong message: got '%s' expected '%s'" %
                    (excMsg, msg)
                    )
        # No need to catch other exceptions: They'll fail the test all by
        # themselves!
        else:   #pragma: no cover
            if hasattr(excClass,'__name__'):
                excName = excClass.__name__
            else:
                excName = str(excClass)
            raise self.failureException(
                "Expected to raise %s, didn't get an exception at all" %
                excName
                )

    def nice_file(self, *fparts):
        """Canonicalize the filename composed of the parts in `fparts`."""
        fname = os.path.join(*fparts)
        return os.path.normcase(os.path.abspath(os.path.realpath(fname)))
    
    def run_command(self, cmd):
        """ Run the command-line `cmd`, print its output.
        """
        # Add our test modules directory to PYTHONPATH.  I'm sure there's too
        # much path munging here, but...
        here = os.path.dirname(self.nice_file(coverage.__file__, ".."))
        testmods = self.nice_file(here, 'test/modules')
        zipfile = self.nice_file(here, 'test/zipmods.zip')
        pypath = os.environ.get('PYTHONPATH', '')
        if pypath:
            pypath += os.pathsep
        pypath += testmods + os.pathsep + zipfile
        os.environ['PYTHONPATH'] = pypath
        
        stdin_unused, stdouterr = os.popen4(cmd)
        output = stdouterr.read()
        print output
        return output

    def assert_equal_sets(self, s1, s2):
        """Assert that the two arguments are equal as sets."""
        self.assertEqual(set(s1), set(s2))
