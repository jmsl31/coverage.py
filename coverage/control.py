"""Core control stuff for coverage.py"""

import glob, os, re, sys, types

from coverage.data import CoverageData
from coverage.misc import nice_pair, CoverageException


class coverage:
    def __init__(self):
        from coverage.collector import Collector
        
        self.parallel_mode = False
        self.exclude_re = ''
        self.nesting = 0
        self.cstack = []
        self.xstack = []
        self.relative_dir = self.abs_file(os.curdir)+os.sep
        
        self.collector = Collector(self.should_trace)
        
        self.data = CoverageData()
    
        # Cache of results of calling the analysis2() method, so that you can
        # specify both -r and -a without doing double work.
        self.analysis_cache = {}
    
        # Cache of results of calling the canonical_filename() method, to
        # avoid duplicating work.
        self.canonical_filename_cache = {}
    
        # The default exclude pattern.
        self.exclude('# *pragma[: ]*[nN][oO] *[cC][oO][vV][eE][rR]')

        # Save coverage data when Python exits.
        import atexit
        atexit.register(self.save)

    def should_trace(self, filename):
        """Decide whether to trace execution in `filename`
        
        Returns a canonicalized filename if it should be traced, False if it
        should not.
        """
        if filename == '<string>':
            # There's no point in ever tracing string executions, we can't do
            # anything with the data later anyway.
            return False
        # TODO: flag: ignore std lib?
        # TODO: ignore by module as well as file?
        return self.canonical_filename(filename)

    def use_cache(self, usecache, cache_file=None):
        self.data.usefile(usecache, cache_file)
        
    def get_ready(self):
        self.collector.reset()
        self.data.read(parallel=self.parallel_mode)
        self.analysis_cache = {}
        
    def start(self):
        self.get_ready()
        if self.nesting == 0:                               #pragma: no cover
            self.collector.start()
        self.nesting += 1
        
    def stop(self):
        self.nesting -= 1
        if self.nesting == 0:                               #pragma: no cover
            self.collector.stop()

    def erase(self):
        self.get_ready()
        self.collector.reset()
        self.analysis_cache = {}
        self.data.erase()

    def exclude(self, regex):
        if self.exclude_re:
            self.exclude_re += "|"
        self.exclude_re += "(" + regex + ")"

    def begin_recursive(self):
        #self.cstack.append(self.c)
        self.xstack.append(self.exclude_re)
        
    def end_recursive(self):
        #self.c = self.cstack.pop()
        self.exclude_re = self.xstack.pop()

    def save(self):
        self.group_collected_data()
        self.data.write()

    def combine(self):
        """Entry point for combining together parallel-mode coverage data."""
        self.data.combine_parallel_data()

    def get_zip_data(self, filename):
        """ Get data from `filename` if it is a zip file path, or return None
            if it is not.
        """
        import zipimport
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
                return data
        return None

    def abs_file(self, filename):
        """ Helper function to turn a filename into an absolute normalized
            filename.
        """
        return os.path.normcase(os.path.abspath(os.path.realpath(filename)))

    def relative_filename(self, filename):
        """ Convert filename to relative filename from self.relative_dir.
        """
        return filename.replace(self.relative_dir, "")

    def canonical_filename(self, filename):
        """Return a canonical filename for `filename`.
        
        An absolute path with no redundant components and normalized case.
        
        """
        if not self.canonical_filename_cache.has_key(filename):
            f = filename
            if os.path.isabs(f) and not os.path.exists(f):
                if not self.get_zip_data(f):
                    f = os.path.basename(f)
            if not os.path.isabs(f):
                for path in [os.curdir] + sys.path:
                    g = os.path.join(path, f)
                    if os.path.exists(g):
                        f = g
                        break
            cf = self.abs_file(f)
            self.canonical_filename_cache[filename] = cf
        return self.canonical_filename_cache[filename]

    def group_collected_data(self):
        """Group the collected data by filename and reset the collector."""
        self.data.add_raw_data(self.collector.data_points())
        self.collector.reset()

    # analyze_morf(morf).  Analyze the module or filename passed as
    # the argument.  If the source code can't be found, raise an error.
    # Otherwise, return a tuple of (1) the canonical filename of the
    # source code for the module, (2) a list of lines of statements
    # in the source code, (3) a list of lines of excluded statements,
    # and (4), a map of line numbers to multi-line line number ranges, for
    # statements that cross lines.

    # The word "morf" means a module object (from which the source file can
    # be deduced by suitable manipulation of the __file__ attribute) or a
    # filename.
    
    def analyze_morf(self, morf):
        from coverage.analyzer import CodeAnalyzer

        if self.analysis_cache.has_key(morf):
            return self.analysis_cache[morf]
        orig_filename = filename = self.morf_filename(morf)
        ext = os.path.splitext(filename)[1]
        source = None
        if ext == '.pyc':
            filename = filename[:-1]
            ext = '.py'
        if ext == '.py':
            if not os.path.exists(filename):
                source = self.get_zip_data(filename)
                if not source:
                    raise CoverageException(
                        "No source for code '%s'." % orig_filename
                        )

        analyzer = CodeAnalyzer()
        lines, excluded_lines, line_map = analyzer.analyze_source(
            text=source, filename=filename, exclude=self.exclude_re
            )

        result = filename, lines, excluded_lines, line_map
        self.analysis_cache[morf] = result
        return result

    # format_lines(statements, lines).  Format a list of line numbers
    # for printing by coalescing groups of lines as long as the lines
    # represent consecutive statements.  This will coalesce even if
    # there are gaps between statements, so if statements =
    # [1,2,3,4,5,10,11,12,13,14] and lines = [1,2,5,10,11,13,14] then
    # format_lines will return "1-2, 5-11, 13-14".

    def format_lines(self, statements, lines):
        pairs = []
        i = 0
        j = 0
        start = None
        pairs = []
        while i < len(statements) and j < len(lines):
            if statements[i] == lines[j]:
                if start == None:
                    start = lines[j]
                end = lines[j]
                j = j + 1
            elif start:
                pairs.append((start, end))
                start = None
            i = i + 1
        if start:
            pairs.append((start, end))
        ret = ', '.join(map(nice_pair, pairs))
        return ret

    # Backward compatibility with version 1.
    def analysis(self, morf):
        f, s, _, m, mf = self.analysis2(morf)
        return f, s, m, mf

    def analysis2(self, morf):
        filename, statements, excluded, line_map = self.analyze_morf(morf)
        self.group_collected_data()
        
        # Identify missing statements.
        missing = []
        execed = self.data.executed_lines(filename)
        for line in statements:
            lines = line_map.get(line)
            if lines:
                for l in range(lines[0], lines[1]+1):
                    if l in execed:
                        break
                else:
                    missing.append(line)
            else:
                if line not in execed:
                    missing.append(line)
                    
        return (filename, statements, excluded, missing,
                self.format_lines(statements, missing))

    # morf_filename(morf).  Return the filename for a module or file.

    def morf_filename(self, morf):
        if hasattr(morf, '__file__'):
            f = morf.__file__
        else:
            f = morf
        return self.canonical_filename(f)

    def morf_name(self, morf):
        """ Return the name of morf as used in report.
        """
        if hasattr(morf, '__name__'):
            return morf.__name__
        else:
            return self.relative_filename(os.path.splitext(morf)[0])

    def filter_by_prefix(self, morfs, omit_prefixes):
        """ Return list of morfs where the morf name does not begin
            with any one of the omit_prefixes.
        """
        filtered_morfs = []
        for morf in morfs:
            for prefix in omit_prefixes:
                if self.morf_name(morf).startswith(prefix):
                    break
            else:
                filtered_morfs.append(morf)

        return filtered_morfs

    def morf_name_compare(self, x, y):
        return cmp(self.morf_name(x), self.morf_name(y))

    def report(self, morfs, show_missing=True, ignore_errors=False, file=None, omit_prefixes=None):
        if not isinstance(morfs, types.ListType):
            morfs = [morfs]
        # On windows, the shell doesn't expand wildcards.  Do it here.
        globbed = []
        for morf in morfs:
            if isinstance(morf, basestring) and ('?' in morf or '*' in morf):
                globbed.extend(glob.glob(morf))
            else:
                globbed.append(morf)
        morfs = globbed

        if omit_prefixes:
            morfs = self.filter_by_prefix(morfs, omit_prefixes)
        morfs.sort(self.morf_name_compare)

        max_name = max(5, max(map(len, map(self.morf_name, morfs))))
        fmt_name = "%%- %ds  " % max_name
        fmt_err = fmt_name + "%s: %s"
        header = fmt_name % "Name" + " Stmts   Exec  Cover"
        fmt_coverage = fmt_name + "% 6d % 6d % 5d%%"
        if show_missing:
            header = header + "   Missing"
            fmt_coverage = fmt_coverage + "   %s"
        if not file:
            file = sys.stdout
        print >>file, header
        print >>file, "-" * len(header)
        total_statements = 0
        total_executed = 0
        for morf in morfs:
            name = self.morf_name(morf)
            try:
                _, statements, _, missing, readable  = self.analysis2(morf)
                n = len(statements)
                m = n - len(missing)
                if n > 0:
                    pc = 100.0 * m / n
                else:
                    pc = 100.0
                args = (name, n, m, pc)
                if show_missing:
                    args = args + (readable,)
                print >>file, fmt_coverage % args
                total_statements = total_statements + n
                total_executed = total_executed + m
            except KeyboardInterrupt:                       #pragma: no cover
                raise
            except:
                if not ignore_errors:
                    typ, msg = sys.exc_info()[:2]
                    print >>file, fmt_err % (name, typ, msg)
        if len(morfs) > 1:
            print >>file, "-" * len(header)
            if total_statements > 0:
                pc = 100.0 * total_executed / total_statements
            else:
                pc = 100.0
            args = ("TOTAL", total_statements, total_executed, pc)
            if show_missing:
                args = args + ("",)
            print >>file, fmt_coverage % args

    # annotate(morfs, ignore_errors).

    blank_re = re.compile(r"\s*(#|$)")
    else_re = re.compile(r"\s*else\s*:\s*(#|$)")

    def annotate(self, morfs, directory=None, ignore_errors=False, omit_prefixes=None):
        if omit_prefixes:
            morfs = self.filter_by_prefix(morfs, omit_prefixes)
        for morf in morfs:
            try:
                filename, statements, excluded, missing, _ = self.analysis2(morf)
                self.annotate_file(filename, statements, excluded, missing, directory)
            except KeyboardInterrupt:
                raise
            except:
                if not ignore_errors:
                    raise
                
    def annotate_file(self, filename, statements, excluded, missing, directory=None):
        source = open(filename, 'r')
        if directory:
            dest_file = os.path.join(directory,
                                     os.path.basename(filename)
                                     + ',cover')
        else:
            dest_file = filename + ',cover'
        dest = open(dest_file, 'w')
        lineno = 0
        i = 0
        j = 0
        covered = True
        while True:
            line = source.readline()
            if line == '':
                break
            lineno = lineno + 1
            while i < len(statements) and statements[i] < lineno:
                i = i + 1
            while j < len(missing) and missing[j] < lineno:
                j = j + 1
            if i < len(statements) and statements[i] == lineno:
                covered = j >= len(missing) or missing[j] > lineno
            if self.blank_re.match(line):
                dest.write('  ')
            elif self.else_re.match(line):
                # Special logic for lines containing only 'else:'.  
                if i >= len(statements) and j >= len(missing):
                    dest.write('! ')
                elif i >= len(statements) or j >= len(missing):
                    dest.write('> ')
                elif statements[i] == missing[j]:
                    dest.write('! ')
                else:
                    dest.write('> ')
            elif lineno in excluded:
                dest.write('- ')
            elif covered:
                dest.write('> ')
            else:
                dest.write('! ')
            dest.write(line)
        source.close()
        dest.close()
