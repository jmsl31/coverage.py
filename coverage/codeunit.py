"""Code unit (module) handling for Coverage."""

import os

from coverage.backward import open_python_source, string_class
from coverage.misc import CoverageException, NoSource
from coverage.parser import CodeParser, PythonParser
from coverage.phystokens import source_token_lines, source_encoding


def code_unit_factory(morfs, file_locator, get_plugin=None):
    """Construct a list of CodeUnits from polymorphic inputs.

    `morfs` is a module or a filename, or a list of same.

    `file_locator` is a FileLocator that can help resolve filenames.

    `get_plugin` is a function taking a filename, and returning a plugin
    responsible for the file.  It can also return None if there is no plugin
    claiming the file.

    Returns a list of CodeUnit objects.

    """
    # Be sure we have a list.
    if not isinstance(morfs, (list, tuple)):
        morfs = [morfs]

    code_units = []
    for morf in morfs:
        plugin = None
        if isinstance(morf, string_class) and get_plugin:
            plugin = get_plugin(morf)
        if plugin:
            file_reporter = plugin.file_reporter(morf)
            if file_reporter is None:
                raise CoverageException(
                    "Plugin %r did not provide a file reporter for %r." % (
                        plugin.plugin_name, morf
                    )
                )
        else:
            file_reporter = PythonCodeUnit(morf, file_locator)
        code_units.append(file_reporter)

    return code_units


class CodeUnit(object):
    """Code unit: a filename or module.

    Instance attributes:

    `name` is a human-readable name for this code unit.
    `filename` is the os path from which we can read the source.
    `relative` is a boolean.

    """

    def __init__(self, morf, file_locator):
        self.file_locator = file_locator

        if hasattr(morf, '__file__'):
            f = morf.__file__
        else:
            f = morf
        f = self._adjust_filename(f)
        self.filename = self.file_locator.canonical_filename(f)

        if hasattr(morf, '__name__'):
            n = modname = morf.__name__
            self.relative = True
        else:
            n = os.path.splitext(morf)[0]
            rel = self.file_locator.relative_filename(n)
            if os.path.isabs(n):
                self.relative = (rel != n)
            else:
                self.relative = True
            n = rel
            modname = None
        self.name = n
        self.modname = modname

        self._source = None

    def __repr__(self):
        return "<CodeUnit name=%r filename=%r>" % (self.name, self.filename)

    def _adjust_filename(self, f):
        # TODO: This shouldn't be in the base class, right?
        return f

    # Annoying comparison operators. Py3k wants __lt__ etc, and Py2k needs all
    # of them defined.

    def __lt__(self, other):
        return self.name < other.name
    def __le__(self, other):
        return self.name <= other.name
    def __eq__(self, other):
        return self.name == other.name
    def __ne__(self, other):
        return self.name != other.name
    def __gt__(self, other):
        return self.name > other.name
    def __ge__(self, other):
        return self.name >= other.name

    def flat_rootname(self):
        """A base for a flat filename to correspond to this code unit.

        Useful for writing files about the code where you want all the files in
        the same directory, but need to differentiate same-named files from
        different directories.

        For example, the file a/b/c.py will return 'a_b_c'

        """
        if self.modname:
            return self.modname.replace('.', '_')
        else:
            root = os.path.splitdrive(self.name)[1]
            return root.replace('\\', '_').replace('/', '_').replace('.', '_')

    def source(self):
        if self._source is None:
            self._source = self.get_source()
        return self._source

    def get_source(self):
        """Return the source code, as a string."""
        if os.path.exists(self.filename):
            # A regular text file: open it.
            with open_python_source(self.filename) as f:
                return f.read()

        # Maybe it's in a zip file?
        source = self.file_locator.get_zip_data(self.filename)
        if source is not None:
            return source

        # Couldn't find source.
        raise CoverageException(
            "No source for code '%s'." % self.filename
            )

    def source_token_lines(self):
        """Return the 'tokenized' text for the code."""
        for line in self.source().splitlines():
            yield [('txt', line)]

    def should_be_python(self):
        """Does it seem like this file should contain Python?

        This is used to decide if a file reported as part of the execution of
        a program was really likely to have contained Python in the first
        place.
        """
        return False

    def get_parser(self, exclude=None):
        raise NotImplementedError


class PythonCodeUnit(CodeUnit):
    """Represents a Python file."""

    def _adjust_filename(self, fname):
        # .pyc files should always refer to a .py instead.
        if fname.endswith(('.pyc', '.pyo')):
            fname = fname[:-1]
        elif fname.endswith('$py.class'): # Jython
            fname = fname[:-9] + ".py"
        return fname

    def get_parser(self, exclude=None):
        actual_filename, source = self._find_source(self.filename)
        return PythonParser(
            text=source, filename=actual_filename, exclude=exclude,
        )

    def _find_source(self, filename):
        """Find the source for `filename`.

        Returns two values: the actual filename, and the source.

        The source returned depends on which of these cases holds:

            * The filename seems to be a non-source file: returns None

            * The filename is a source file, and actually exists: returns None.

            * The filename is a source file, and is in a zip file or egg:
              returns the source.

            * The filename is a source file, but couldn't be found: raises
              `NoSource`.

        """
        source = None

        base, ext = os.path.splitext(filename)
        TRY_EXTS = {
            '.py':  ['.py', '.pyw'],
            '.pyw': ['.pyw'],
        }
        try_exts = TRY_EXTS.get(ext)
        if not try_exts:
            return filename, None

        for try_ext in try_exts:
            try_filename = base + try_ext
            if os.path.exists(try_filename):
                return try_filename, None
            source = self.file_locator.get_zip_data(try_filename)
            if source:
                return try_filename, source
        raise NoSource("No source for code: '%s'" % filename)

    def should_be_python(self):
        """Does it seem like this file should contain Python?

        This is used to decide if a file reported as part of the execution of
        a program was really likely to have contained Python in the first
        place.

        """
        # Get the file extension.
        _, ext = os.path.splitext(self.filename)

        # Anything named *.py* should be Python.
        if ext.startswith('.py'):
            return True
        # A file with no extension should be Python.
        if not ext:
            return True
        # Everything else is probably not Python.
        return False

    def source_token_lines(self):
        return source_token_lines(self.source())

    def source_encoding(self):
        return source_encoding(self.source())
