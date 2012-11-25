"""Helper for building, testing, and linting coverage.py.

To get portability, all these operations are written in Python here instead
of in shell scripts, batch files, or Makefiles.

"""

import fnmatch
import glob
import os
import platform
import sys
import zipfile


def do_remove_extension(args):
    """Remove the compiled C extension, no matter what its name."""

    so_patterns = """
        tracer.so
        tracer.*.so
        tracer.pyd
        """.split()

    for pattern in so_patterns:
        pattern = os.path.join("coverage", pattern)
        for filename in glob.glob(pattern):
            try:
                os.remove(filename)
            except OSError:
                pass

def run_tests(args):
    """The actual running of tests."""
    import nose.core
    tracer = args[0]
    if tracer == "py":
        label = "with Python tracer"
    else:
        label = "with C tracer"
        if os.environ.get("COVERAGE_NO_EXTENSION"):
            print("Skipping tests, no C extension in this environment")
            return
    print_banner(label)
    os.environ["COVERAGE_TEST_TRACER"] = tracer
    nose_args = ["nosetests"] + args[1:]
    nose.core.main(argv=nose_args)

def run_tests_with_coverage(args):
    """Run tests, but with coverage."""
    import coverage

    os.environ['COVERAGE_COVERAGE'] = 'yes please'
    os.environ['COVERAGE_PROCESS_START'] = os.path.abspath('metacov.ini')

    # Create the .pth file that will let us measure coverage in sub-processes.
    import nose
    pth_path = os.path.join(os.path.dirname(os.path.dirname(nose.__file__)), "covcov.pth")
    pth_file = open(pth_path, "w")
    try:
        pth_file.write("import coverage; coverage.process_startup()\n")
    finally:
        pth_file.close()

    tracer = os.environ.get('COVERAGE_TEST_TRACER', 'c')
    version = "%s%s" % sys.version_info[:2]
    suffix = "%s_%s" % (version, tracer)

    cov = coverage.coverage(config_file="metacov.ini", data_suffix=suffix)
    # Cheap trick: the coverage code itself is excluded from measurement, but
    # if we clobber the cover_prefix in the coverage object, we can defeat the
    # self-detection.
    cov.cover_prefix = "Please measure coverage.py!"
    cov.erase()
    cov.start()

    try:
        # Re-import coverage to get it coverage tested!  I don't understand all the
        # mechanics here, but if I don't carry over the imported modules (in
        # covmods), then things go haywire (os == None, eventually).
        covmods = {}
        covdir = os.path.split(coverage.__file__)[0]
        # We have to make a list since we'll be deleting in the loop.
        modules = list(sys.modules.items())
        for name, mod in modules:
            if name.startswith('coverage'):
                if hasattr(mod, '__file__') and mod.__file__.startswith(covdir):
                    covmods[name] = mod
                    del sys.modules[name]
        import coverage     # don't warn about re-import: pylint: disable=W0404
        sys.modules.update(covmods)

        # Run nosetests, with the arguments from our command line.
        print(":: Running nosetests")
        try:
            run_tests(args)
        except SystemExit:
            # nose3 seems to raise SystemExit, not sure why?
            pass
    finally:
        cov.stop()
        os.remove(pth_path)

    print(":: Saving data %s" % suffix)
    cov.save()

def do_combine_html(args):
    import coverage
    cov = coverage.coverage(config_file="metacov.ini")
    cov.combine()
    cov.save()
    cov.html_report()

def do_test_with_tracer(args):
    """Run nosetests with a particular tracer."""
    if os.environ.get("COVERAGE_COVERAGE", ""):
        return run_tests_with_coverage(args)
    else:
        return run_tests(args)

def do_zip_mods(args):
    """Build the zipmods.zip file."""
    zf = zipfile.ZipFile("test/zipmods.zip", "w")
    zf.write("test/covmodzip1.py", "covmodzip1.py")
    zf.close()

def do_check_eol(args):
    """Check files for incorrect newlines and trailing whitespace."""

    ignore_dirs = [
        '.svn', '.hg', '.tox', '.tox_kits', 'coverage.egg-info',
        '_build',
        ]
    checked = set([])

    def check_file(fname, crlf=True, trail_white=True):
        fname = os.path.relpath(fname)
        if fname in checked:
            return
        checked.add(fname)

        line = None
        for n, line in enumerate(open(fname, "rb")):
            if crlf:
                if "\r" in line:
                    print("%s@%d: CR found" % (fname, n+1))
                    return
            if trail_white:
                line = line[:-1]
                if not crlf:
                    line = line.rstrip('\r')
                if line.rstrip() != line:
                    print("%s@%d: trailing whitespace found" % (fname, n+1))
                    return

        if line is not None and not line.strip():
            print("%s: final blank line" % (fname,))

    def check_files(root, patterns, **kwargs):
        for root, dirs, files in os.walk(root):
            for f in files:
                fname = os.path.join(root, f)
                for p in patterns:
                    if fnmatch.fnmatch(fname, p):
                        check_file(fname, **kwargs)
                        break
            for dir_name in ignore_dirs:
                if dir_name in dirs:
                    dirs.remove(dir_name)

    check_files("coverage", ["*.py", "*.c"])
    check_files("coverage/htmlfiles", ["*.html", "*.css", "*.js"])
    check_file("test/farm/html/src/bom.py", crlf=False)
    check_files("test", ["*.py"])
    check_files("test", ["*,cover"], trail_white=False)
    check_files("test/js", ["*.js", "*.html"])
    check_file("setup.py")
    check_files("doc", ["*.rst"])
    check_files(".", ["*.txt"])


def print_banner(label):
    """Print the version of Python."""
    try:
        impl = platform.python_implementation()
    except AttributeError:
        impl = "Python"

    version = platform.python_version()

    if '__pypy__' in sys.builtin_module_names:
        pypy_version = ".".join([str(v) for v in sys.pypy_version_info])
        version += " (pypy %s)" % pypy_version

    print('=== %s %s %s (%s) ===' % (impl, version, label, sys.executable))


def do_help(args):
    """List the available commands"""
    items = globals().items()
    items.sort()
    for name, value in items:
        if name.startswith('do_'):
            print("%-20s%s" % (name[3:], value.__doc__))


def main(args):
    handler = globals().get('do_'+args[0])
    if handler is None:
        print("*** No handler for %r" % args[0])
        return 1
    return handler(args[1:])

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
