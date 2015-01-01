"""Python source expertise for coverage.py"""

import os.path
import sys
import tokenize
import zipimport

from coverage.backward import unicode_class
from coverage.codeunit import CodeUnit
from coverage.misc import NoSource
from coverage.parser import PythonParser
from coverage.phystokens import source_token_lines, source_encoding


def read_python_source(filename):
    """Read the Python source text from `filename`.

    Returns a str: unicode on Python 3, bytes on Python 2.

    """
    # Python 3.2 provides `tokenize.open`, the best way to open source files.
    if sys.version_info >= (3, 2):
        f = tokenize.open(filename)
    else:
        f = open(filename, "rU")

    with f:
        return f.read()


def get_python_source(filename):
    """Return the source code, as a str."""
    base, ext = os.path.splitext(filename)
    if ext == ".py" and sys.platform == "win32":
        exts = [".py", ".pyw"]
    else:
        exts = [ext]

    for ext in exts:
        try_filename = base + ext
        if os.path.exists(try_filename):
            # A regular text file: open it.
            source = read_python_source(try_filename)
            break

        # Maybe it's in a zip file?
        source = get_zip_bytes(try_filename)
        if source is not None:
            if sys.version_info >= (3, 0):
                source = source.decode(source_encoding(source))
            break
    else:
        # Couldn't find source.
        raise NoSource("No source for code: '%s'." % filename)

    # Python code should always end with a line with a newline.
    if source and source[-1] != '\n':
        source += '\n'

    return source


def get_zip_bytes(filename):
    """Get data from `filename` if it is a zip file path.

    Returns the bytestring data read from the zip file, or None if no zip file
    could be found or `filename` isn't in it.  The data returned will be
    an empty string if the file is empty.

    """
    markers = ['.zip'+os.sep, '.egg'+os.sep]
    for marker in markers:
        if marker in filename:
            parts = filename.split(marker)
            try:
                zi = zipimport.zipimporter(parts[0]+marker[:-1])
            except zipimport.ZipImportError:
                continue
            try:
                data = zi.get_data(parts[1])
            except IOError:
                continue
            assert isinstance(data, bytes)
            return data
    return None


class PythonCodeUnit(CodeUnit):
    """Represents a Python file."""

    def __init__(self, morf, file_locator=None):
        super(PythonCodeUnit, self).__init__(morf, file_locator)
        self._source = None

    def _adjust_filename(self, fname):
        # .pyc files should always refer to a .py instead.
        if fname.endswith(('.pyc', '.pyo')):
            fname = fname[:-1]
        elif fname.endswith('$py.class'):   # Jython
            fname = fname[:-9] + ".py"
        return fname

    def source(self):
        if self._source is None:
            self._source = get_python_source(self.filename)
            if sys.version_info < (3, 0):
                encoding = source_encoding(self._source)
                self._source = self._source.decode(encoding, "replace")
            assert isinstance(self._source, unicode_class)
        return self._source

    def get_parser(self, exclude=None):
        return PythonParser(filename=self.filename, exclude=exclude)

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
