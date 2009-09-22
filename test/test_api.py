"""Tests for Coverage's api."""

import os, re, sys, textwrap

import coverage
from coverage.backward import StringIO

sys.path.insert(0, os.path.split(__file__)[0]) # Force relative import for Py3k
from coveragetest import CoverageTest


class ApiTest(CoverageTest):
    """Api-oriented tests for Coverage."""

    def testSimple(self):
        coverage.erase()

        self.makeFile("mycode.py", """\
            a = 1
            b = 2
            if b == 3:
                c = 4
            d = 5
            """)
            
        # Import the python file, executing it.
        coverage.start()
        self.importModule("mycode")
        coverage.stop()
    
        _, statements, missing, missingtext = coverage.analysis("mycode.py")
        self.assertEqual(statements, [1,2,3,4,5])
        self.assertEqual(missing, [4])
        self.assertEqual(missingtext, "4")
        
    def doReportWork(self, modname):
        """Create a module named `modname`, then measure it."""
        coverage.erase()

        self.makeFile(modname+".py", """\
            a = 1
            b = 2
            if b == 3:
                c = 4
                d = 5
                e = 6
            f = 7
            """)
            
        # Import the python file, executing it.
        coverage.start()
        self.importModule(modname)
        coverage.stop()
        
    def testReport(self):
        self.doReportWork("mycode2")
        coverage.report(["mycode2.py"])
        self.assertEqual(self.stdout(), textwrap.dedent("""\
            Name      Stmts   Exec  Cover   Missing
            ---------------------------------------
            mycode2       7      4    57%   4-6
            """))
        
    def testReportFile(self):
        # The file= argument of coverage.report makes the report go there.
        self.doReportWork("mycode3")
        fout = StringIO()
        coverage.report(["mycode3.py"], file=fout)
        self.assertEqual(self.stdout(), "")
        self.assertEqual(fout.getvalue(), textwrap.dedent("""\
            Name      Stmts   Exec  Cover   Missing
            ---------------------------------------
            mycode3       7      4    57%   4-6
            """))

    def testReportDefault(self):
        # Calling report() with no morfs will report on whatever was executed.
        self.doReportWork("mycode4")
        coverage.report()
        rpt = re.sub(r"\s+", " ", self.stdout())
        self.assert_("mycode4 7 4 57% 4-6" in rpt)

    def testUnexecutedFile(self):
        cov = coverage.coverage()

        self.makeFile("mycode.py", """\
            a = 1
            b = 2
            if b == 3:
                c = 4
            d = 5
            """)
            
        self.makeFile("not_run.py", """\
            fooey = 17
            """)
            
        # Import the python file, executing it.
        cov.start()
        self.importModule("mycode")
        cov.stop()
    
        _, statements, missing, _ = cov.analysis("not_run.py")
        self.assertEqual(statements, [1])
        self.assertEqual(missing, [1])

    def testFileNames(self):

        self.makeFile("mymain.py", """\
            import mymod
            a = 1
            """)
            
        self.makeFile("mymod.py", """\
            fooey = 17
            """)
            
        # Import the python file, executing it.
        cov = coverage.coverage()
        cov.start()
        self.importModule("mymain")
        cov.stop()
    
        filename, _, _, _ = cov.analysis("mymain.py")
        self.assertEqual(os.path.basename(filename), "mymain.py")
        filename, _, _, _ = cov.analysis("mymod.py")
        self.assertEqual(os.path.basename(filename), "mymod.py")
        
        filename, _, _, _ = cov.analysis(sys.modules["mymain"])
        self.assertEqual(os.path.basename(filename), "mymain.py")
        filename, _, _, _ = cov.analysis(sys.modules["mymod"])
        self.assertEqual(os.path.basename(filename), "mymod.py")

        # Import the python file, executing it again, once it's been compiled
        # already.
        cov = coverage.coverage()
        cov.start()
        self.importModule("mymain")
        cov.stop()
    
        filename, _, _, _ = cov.analysis("mymain.py")
        self.assertEqual(os.path.basename(filename), "mymain.py")
        filename, _, _, _ = cov.analysis("mymod.py")
        self.assertEqual(os.path.basename(filename), "mymod.py")
        
        filename, _, _, _ = cov.analysis(sys.modules["mymain"])
        self.assertEqual(os.path.basename(filename), "mymain.py")
        filename, _, _, _ = cov.analysis(sys.modules["mymod"])
        self.assertEqual(os.path.basename(filename), "mymod.py")

    def testIgnoreStdLib(self):
        self.makeFile("mymain.py", """\
            import mymod, colorsys
            a = 1
            hls = colorsys.rgb_to_hls(1.0, 0.5, 0.0)
            """)
            
        self.makeFile("mymod.py", """\
            fooey = 17
            """)

        # Measure without the stdlib.
        cov1 = coverage.coverage()
        self.assertEqual(cov1.cover_pylib, False)
        cov1.start()
        self.importModule("mymain")
        cov1.stop()

        # some statements were marked executed in mymain.py
        _, statements, missing, _ = cov1.analysis("mymain.py")
        self.assertNotEqual(statements, missing)
        # but none were in colorsys.py
        _, statements, missing, _ = cov1.analysis("colorsys.py")
        self.assertEqual(statements, missing)

        # Measure with the stdlib.
        cov2 = coverage.coverage(cover_pylib=True)
        cov2.start()
        self.importModule("mymain")
        cov2.stop()

        # some statements were marked executed in mymain.py
        _, statements, missing, _ = cov2.analysis("mymain.py")
        self.assertNotEqual(statements, missing)
        # and some were marked executed in colorsys.py
        _, statements, missing, _ = cov2.analysis("colorsys.py")
        self.assertNotEqual(statements, missing)

    def testExcludeList(self):
        cov = coverage.coverage()
        cov.clear_exclude()
        self.assertEqual(cov.get_exclude_list(), [])
        cov.exclude("foo")
        self.assertEqual(cov.get_exclude_list(), ["foo"])
        cov.exclude("bar")
        self.assertEqual(cov.get_exclude_list(), ["foo", "bar"])
        self.assertEqual(cov.exclude_re, "(foo)|(bar)")
        cov.clear_exclude()
        self.assertEqual(cov.get_exclude_list(), [])

    def testDatafileDefault(self):
        # Default data file behavior: it's .coverage
        self.makeFile("datatest1.py", """\
            fooey = 17
            """)

        self.assert_equal_sets(os.listdir("."), ["datatest1.py"])
        cov = coverage.coverage()
        cov.start()
        self.importModule("datatest1")
        cov.stop()
        cov.save()
        self.assert_equal_sets(os.listdir("."),
                            ["datatest1.py", "datatest1.pyc", ".coverage"])

    def testDatafileSpecified(self):
        # You can specify the data file name.
        self.makeFile("datatest2.py", """\
            fooey = 17
            """)

        self.assert_equal_sets(os.listdir("."), ["datatest2.py"])
        cov = coverage.coverage(data_file="cov.data")
        cov.start()
        self.importModule("datatest2")
        cov.stop()
        cov.save()
        self.assert_equal_sets(os.listdir("."),
                            ["datatest2.py", "datatest2.pyc", "cov.data"])

    def testDatafileAndSuffixSpecified(self):
        # You can specify the data file name and suffix.
        self.makeFile("datatest3.py", """\
            fooey = 17
            """)

        self.assert_equal_sets(os.listdir("."), ["datatest3.py"])
        cov = coverage.coverage(data_file="cov.data", data_suffix=".14")
        cov.start()
        self.importModule("datatest3")
        cov.stop()
        cov.save()
        self.assert_equal_sets(os.listdir("."),
                            ["datatest3.py", "datatest3.pyc", "cov.data.14"])

    def testEmptyReporting(self):
        # Used to be you'd get an exception reporting on nothing...
        cov = coverage.coverage()
        cov.erase()
        cov.report()
