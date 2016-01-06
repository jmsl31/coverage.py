# Licensed under the Apache License: http://www.apache.org/licenses/LICENSE-2.0
# For details: https://bitbucket.org/ned/coveragepy/src/default/NOTICE.txt

"""Code parsing for coverage.py."""

import ast
import collections
import dis
import os
import re
import token
import tokenize

from coverage import env
from coverage.backward import range    # pylint: disable=redefined-builtin
from coverage.backward import bytes_to_ints, string_class
from coverage.bytecode import ByteCodes, CodeObjects
from coverage.misc import contract, nice_pair, join_regex
from coverage.misc import CoverageException, NoSource, NotPython
from coverage.phystokens import compile_unicode, generate_tokens, neuter_encoding_declaration


class PythonParser(object):
    """Parse code to find executable lines, excluded lines, etc."""

    @contract(text='unicode|None')
    def __init__(self, text=None, filename=None, exclude=None):
        """
        Source can be provided as `text`, the text itself, or `filename`, from
        which the text will be read.  Excluded lines are those that match
        `exclude`, a regex.

        """
        assert text or filename, "PythonParser needs either text or filename"
        self.filename = filename or "<code>"
        self.text = text
        if not self.text:
            from coverage.python import get_python_source
            try:
                self.text = get_python_source(self.filename)
            except IOError as err:
                raise NoSource(
                    "No source for code: '%s': %s" % (self.filename, err)
                )

        self.exclude = exclude

        # The text lines of the parsed code.
        self.lines = self.text.split('\n')

        # The normalized line numbers of the statements in the code. Exclusions
        # are taken into account, and statements are adjusted to their first
        # lines.
        self.statements = set()

        # The normalized line numbers of the excluded lines in the code,
        # adjusted to their first lines.
        self.excluded = set()

        # The raw_* attributes are only used in this class, and in
        # lab/parser.py to show how this class is working.

        # The line numbers that start statements, as reported by the line
        # number table in the bytecode.
        self.raw_statements = set()

        # The raw line numbers of excluded lines of code, as marked by pragmas.
        self.raw_excluded = set()

        # The line numbers of class and function definitions.
        self.raw_classdefs = set()
        self.raw_funcdefs = set()

        # The line numbers of docstring lines.
        self.raw_docstrings = set()

        # Internal detail, used by lab/parser.py.
        self.show_tokens = False

        # A dict mapping line numbers to (lo,hi) for multi-line statements.
        self._multiline = {}

        # Lazily-created ByteParser and arc data.
        self._byte_parser = None
        self._all_arcs = None

    @property
    def byte_parser(self):
        """Create a ByteParser on demand."""
        if not self._byte_parser:
            self._byte_parser = ByteParser(self.text, filename=self.filename)
        return self._byte_parser

    def lines_matching(self, *regexes):
        """Find the lines matching one of a list of regexes.

        Returns a set of line numbers, the lines that contain a match for one
        of the regexes in `regexes`.  The entire line needn't match, just a
        part of it.

        """
        combined = join_regex(regexes)
        if env.PY2:
            combined = combined.decode("utf8")
        regex_c = re.compile(combined)
        matches = set()
        for i, ltext in enumerate(self.lines, start=1):
            if regex_c.search(ltext):
                matches.add(i)
        return matches

    def _raw_parse(self):
        """Parse the source to find the interesting facts about its lines.

        A handful of attributes are updated.

        """
        # Find lines which match an exclusion pattern.
        if self.exclude:
            self.raw_excluded = self.lines_matching(self.exclude)

        # Tokenize, to find excluded suites, to find docstrings, and to find
        # multi-line statements.
        indent = 0
        exclude_indent = 0
        excluding = False
        excluding_decorators = False
        prev_toktype = token.INDENT
        first_line = None
        empty = True
        first_on_line = True

        tokgen = generate_tokens(self.text)
        for toktype, ttext, (slineno, _), (elineno, _), ltext in tokgen:
            if self.show_tokens:                # pragma: not covered
                print("%10s %5s %-20r %r" % (
                    tokenize.tok_name.get(toktype, toktype),
                    nice_pair((slineno, elineno)), ttext, ltext
                ))
            if toktype == token.INDENT:
                indent += 1
            elif toktype == token.DEDENT:
                indent -= 1
            elif toktype == token.NAME:
                if ttext == 'class':
                    # Class definitions look like branches in the byte code, so
                    # we need to exclude them.  The simplest way is to note the
                    # lines with the 'class' keyword.
                    self.raw_classdefs.add(slineno)
                elif ttext == 'def':
                    self.raw_funcdefs.add(slineno)
            elif toktype == token.OP:
                if ttext == ':':
                    should_exclude = (elineno in self.raw_excluded) or excluding_decorators
                    if not excluding and should_exclude:
                        # Start excluding a suite.  We trigger off of the colon
                        # token so that the #pragma comment will be recognized on
                        # the same line as the colon.
                        self.raw_excluded.add(elineno)
                        exclude_indent = indent
                        excluding = True
                        excluding_decorators = False
                elif ttext == '@' and first_on_line:
                    # A decorator.
                    if elineno in self.raw_excluded:
                        excluding_decorators = True
                    if excluding_decorators:
                        self.raw_excluded.add(elineno)
            elif toktype == token.STRING and prev_toktype == token.INDENT:
                # Strings that are first on an indented line are docstrings.
                # (a trick from trace.py in the stdlib.) This works for
                # 99.9999% of cases.  For the rest (!) see:
                # http://stackoverflow.com/questions/1769332/x/1769794#1769794
                self.raw_docstrings.update(range(slineno, elineno+1))
            elif toktype == token.NEWLINE:
                if first_line is not None and elineno != first_line:
                    # We're at the end of a line, and we've ended on a
                    # different line than the first line of the statement,
                    # so record a multi-line range.
                    for l in range(first_line, elineno+1):
                        self._multiline[l] = first_line
                first_line = None
                first_on_line = True

            if ttext.strip() and toktype != tokenize.COMMENT:
                # A non-whitespace token.
                empty = False
                if first_line is None:
                    # The token is not whitespace, and is the first in a
                    # statement.
                    first_line = slineno
                    # Check whether to end an excluded suite.
                    if excluding and indent <= exclude_indent:
                        excluding = False
                    if excluding:
                        self.raw_excluded.add(elineno)
                    first_on_line = False

            prev_toktype = toktype

        # Find the starts of the executable statements.
        if not empty:
            self.raw_statements.update(self.byte_parser._find_statements())

    def first_line(self, line):
        """Return the first line number of the statement including `line`."""
        first_line = self._multiline.get(line)
        if first_line:
            return first_line
        else:
            return line

    def first_lines(self, lines):
        """Map the line numbers in `lines` to the correct first line of the
        statement.

        Returns a set of the first lines.

        """
        return set(self.first_line(l) for l in lines)

    def translate_lines(self, lines):
        """Implement `FileReporter.translate_lines`."""
        return self.first_lines(lines)

    def translate_arcs(self, arcs):
        """Implement `FileReporter.translate_arcs`."""
        return [(self.first_line(a), self.first_line(b)) for (a, b) in arcs]

    def parse_source(self):
        """Parse source text to find executable lines, excluded lines, etc.

        Sets the .excluded and .statements attributes, normalized to the first
        line of multi-line statements.

        """
        try:
            self._raw_parse()
        except (tokenize.TokenError, IndentationError) as err:
            if hasattr(err, "lineno"):
                lineno = err.lineno         # IndentationError
            else:
                lineno = err.args[1][0]     # TokenError
            raise NotPython(
                u"Couldn't parse '%s' as Python source: '%s' at line %d" % (
                    self.filename, err.args[0], lineno
                )
            )

        self.excluded = self.first_lines(self.raw_excluded)

        ignore = self.excluded | self.raw_docstrings
        starts = self.raw_statements - ignore
        self.statements = self.first_lines(starts) - ignore

    def old_arcs(self):
        """Get information about the arcs available in the code.

        Returns a set of line number pairs.  Line numbers have been normalized
        to the first line of multi-line statements.

        """
        if self._all_arcs is None:
            self._all_arcs = set()
            for l1, l2 in self.byte_parser._all_arcs():
                fl1 = self.first_line(l1)
                fl2 = self.first_line(l2)
                if fl1 != fl2:
                    self._all_arcs.add((fl1, fl2))
        return self._all_arcs

    def arcs(self):
        if self._all_arcs is None:
            aaa = AstArcAnalyzer(self.text, self.raw_funcdefs, self.raw_classdefs)
            arcs = aaa.collect_arcs()

            self._all_arcs = set()
            for l1, l2 in arcs:
                fl1 = self.first_line(l1)
                fl2 = self.first_line(l2)
                if fl1 != fl2:
                    self._all_arcs.add((fl1, fl2))
        return self._all_arcs

    def exit_counts(self):
        """Get a count of exits from that each line.

        Excluded lines are excluded.

        """
        exit_counts = collections.defaultdict(int)
        for l1, l2 in self.arcs():
            if l1 < 0:
                # Don't ever report -1 as a line number
                continue
            if l1 in self.excluded:
                # Don't report excluded lines as line numbers.
                continue
            if l2 in self.excluded:
                # Arcs to excluded lines shouldn't count.
                continue
            exit_counts[l1] += 1

        # Class definitions have one extra exit, so remove one for each:
        for l in self.raw_classdefs:
            # Ensure key is there: class definitions can include excluded lines.
            if l in exit_counts:
                exit_counts[l] -= 1

        return exit_counts


class LoopBlock(object):
    def __init__(self, start):
        self.start = start
        self.break_exits = set()

class FunctionBlock(object):
    def __init__(self, start):
        self.start = start

class TryBlock(object):
    def __init__(self, handler_start=None, final_start=None):
        self.handler_start = handler_start  # TODO: is this used?
        self.final_start = final_start      # TODO: is this used?
        self.break_from = set()
        self.continue_from = set()
        self.return_from = set()
        self.raise_from = set()


class AstArcAnalyzer(object):
    @contract(text='unicode', funcdefs=set, classdefs=set)
    def __init__(self, text, funcdefs, classdefs):
        self.root_node = ast.parse(neuter_encoding_declaration(text))
        self.funcdefs = funcdefs
        self.classdefs = classdefs

        if int(os.environ.get("COVERAGE_ASTDUMP", 0)):      # pragma: debugging
            # Dump the AST so that failing tests have helpful output.
            ast_dump(self.root_node)

        self.arcs = None
        self.block_stack = []

    def collect_arcs(self):
        self.arcs = set()
        self.add_arcs_for_code_objects(self.root_node)
        return self.arcs

    def blocks(self):
        """Yield the blocks in nearest-to-farthest order."""
        return reversed(self.block_stack)

    def line_for_node(self, node):
        """What is the right line number to use for this node?"""
        node_name = node.__class__.__name__
        handler = getattr(self, "_line__" + node_name, None)
        if handler is not None:
            return handler(node)
        else:
            return node.lineno

    def _line__Assign(self, node):
        return self.line_for_node(node.value)

    def _line__Dict(self, node):
        # Python 3.5 changed how dict literals are made.
        if env.PYVERSION >= (3, 5) and node.keys:
            return node.keys[0].lineno
        else:
            return node.lineno

    def _line__List(self, node):
        if node.elts:
            return self.line_for_node(node.elts[0])
        else:
            return node.lineno

    def _line__Module(self, node):
        if node.body:
            return self.line_for_node(node.body[0])
        else:
            # Modules have no line number, they always start at 1.
            return 1

    OK_TO_DEFAULT = set([
        "Assign", "Assert", "AugAssign", "Delete", "Exec", "Expr", "Global",
        "Import", "ImportFrom", "Pass", "Print",
    ])

    def add_arcs(self, node):
        """Add the arcs for `node`.

        Return a set of line numbers, exits from this node to the next.
        """
        # Yield-froms and awaits can appear anywhere.
        # TODO: this is probably over-doing it, and too expensive. Can we
        # instrument the ast walking to see how many nodes we are revisiting?
        if isinstance(node, ast.stmt):
            for _, value in ast.iter_fields(node):
                if isinstance(value, ast.expr) and self.contains_return_expression(value):
                    self.process_return_exits([self.line_for_node(node)])
                    break

        node_name = node.__class__.__name__
        handler = getattr(self, "_handle__" + node_name, None)
        if handler is not None:
            return handler(node)

        if 0:
            node_name = node.__class__.__name__
            if node_name not in self.OK_TO_DEFAULT:
                print("*** Unhandled: {0}".format(node))
        return set([self.line_for_node(node)])

    def add_body_arcs(self, body, from_line=None, prev_lines=None):
        if prev_lines is None:
            prev_lines = set([from_line])
        for body_node in body:
            lineno = self.line_for_node(body_node)
            for prev_lineno in prev_lines:
                self.arcs.add((prev_lineno, lineno))
            prev_lines = self.add_arcs(body_node)
        return prev_lines

    def is_constant_expr(self, node):
        """Is this a compile-time constant?"""
        node_name = node.__class__.__name__
        if node_name in ["NameConstant", "Num"]:
            return True
        elif node_name == "Name":
            if env.PY3 and node.id in ["True", "False", "None"]:
                return True
        return False

    # tests to write:
    # TODO: while EXPR:
    # TODO: while False:
    # TODO: listcomps hidden deep in other expressions
    # TODO: listcomps hidden in lists: x = [[i for i in range(10)]]
    # TODO: nested function definitions

    def process_break_exits(self, exits):
        for block in self.blocks():
            if isinstance(block, LoopBlock):
                block.break_exits.update(exits)
                break
            elif isinstance(block, TryBlock) and block.final_start:
                block.break_from.update(exits)
                break

    def process_continue_exits(self, exits):
        for block in self.blocks():
            if isinstance(block, LoopBlock):
                for xit in exits:
                    self.arcs.add((xit, block.start))
                break
            elif isinstance(block, TryBlock) and block.final_start:
                block.continue_from.update(exits)
                break

    def process_raise_exits(self, exits):
        for block in self.blocks():
            if isinstance(block, TryBlock):
                if block.handler_start:
                    for xit in exits:
                        self.arcs.add((xit, block.handler_start))
                    break
                elif block.final_start:
                    block.raise_from.update(exits)
                    break
            elif isinstance(block, FunctionBlock):
                for xit in exits:
                    self.arcs.add((xit, -block.start))
                break

    def process_return_exits(self, exits):
        for block in self.blocks():
            if isinstance(block, TryBlock) and block.final_start:
                block.return_from.update(exits)
                break
            elif isinstance(block, FunctionBlock):
                for xit in exits:
                    self.arcs.add((xit, -block.start))
                break

    ## Handlers

    def _handle__Break(self, node):
        here = self.line_for_node(node)
        self.process_break_exits([here])
        return set()

    def _handle__ClassDef(self, node):
        return self.process_decorated(node, self.classdefs)

    def process_decorated(self, node, defs):
        last = self.line_for_node(node)
        if node.decorator_list:
            for dec_node in node.decorator_list:
                dec_start = self.line_for_node(dec_node)
                if dec_start != last:
                    self.arcs.add((last, dec_start))
                last = dec_start
        # The definition line may have been missed, but we should have it in
        # `defs`.
        body_start = self.line_for_node(node.body[0])
        for lineno in range(last+1, body_start):
            if lineno in defs:
                self.arcs.add((last, lineno))
                last = lineno
        # the body is handled in add_arcs_for_code_objects.
        return set([last])

    def _handle__Continue(self, node):
        here = self.line_for_node(node)
        self.process_continue_exits([here])
        return set()

    def _handle__For(self, node):
        start = self.line_for_node(node.iter)
        self.block_stack.append(LoopBlock(start=start))
        exits = self.add_body_arcs(node.body, from_line=start)
        for xit in exits:
            self.arcs.add((xit, start))
        my_block = self.block_stack.pop()
        exits = my_block.break_exits
        if node.orelse:
            else_exits = self.add_body_arcs(node.orelse, from_line=start)
            exits |= else_exits
        else:
            # no else clause: exit from the for line.
            exits.add(start)
        return exits

    _handle__AsyncFor = _handle__For

    def _handle__FunctionDef(self, node):
        return self.process_decorated(node, self.funcdefs)

    _handle__AsyncFunctionDef = _handle__FunctionDef

    def _handle__If(self, node):
        start = self.line_for_node(node.test)
        exits = self.add_body_arcs(node.body, from_line=start)
        exits |= self.add_body_arcs(node.orelse, from_line=start)
        return exits

    def _handle__Raise(self, node):
        # `raise` statement jumps away, no exits from here.
        here = self.line_for_node(node)
        self.process_raise_exits([here])
        return set()

    def _handle__Return(self, node):
        here = self.line_for_node(node)
        self.process_return_exits([here])
        return set()

    def _handle__Try(self, node):
        # try/finally is tricky. If there's a finally clause, then we need a
        # FinallyBlock to track what flows might go through the finally instead
        # of their normal flow.
        if node.handlers:
            handler_start = self.line_for_node(node.handlers[0])
        else:
            handler_start = None

        if node.finalbody:
            final_start = self.line_for_node(node.finalbody[0])
        else:
            final_start = None

        self.block_stack.append(TryBlock(handler_start=handler_start, final_start=final_start))

        start = self.line_for_node(node)
        exits = self.add_body_arcs(node.body, from_line=start)

        try_block = self.block_stack.pop()
        handler_exits = set()
        last_handler_start = None
        if node.handlers:
            for handler_node in node.handlers:
                handler_start = self.line_for_node(handler_node)
                if last_handler_start is not None:
                    self.arcs.add((last_handler_start, handler_start))
                last_handler_start = handler_start
                handler_exits |= self.add_body_arcs(handler_node.body, from_line=handler_start)
                if handler_node.type is None:
                    # "except:" doesn't jump to subsequent handlers, or
                    # "finally:".
                    last_handler_start = None
                    # TODO: should we break here? Handlers after "except:"
                    # won't be run.  Should coverage know that code can't be
                    # run, or should it flag it as not run?

        if node.orelse:
            exits = self.add_body_arcs(node.orelse, prev_lines=exits)

        exits |= handler_exits
        if node.finalbody:
            final_from = (                  # You can get to the `finally` clause from:
                exits |                         # the exits of the body or `else` clause,
                try_block.break_from |          # or a `break` in the body,
                try_block.continue_from |       # or a `continue` in the body,
                try_block.return_from           # or a `return` in the body.
            )
            if node.handlers and last_handler_start is not None:
                # If there was an "except X:" clause, then a "raise" in the
                # body goes to the "except X:" before the "finally", but the
                # "except" go to the finally.
                final_from.add(last_handler_start)
            else:
                final_from |= try_block.raise_from
            exits = self.add_body_arcs(node.finalbody, prev_lines=final_from)
            if try_block.break_from:
                self.process_break_exits(exits)
            if try_block.continue_from:
                self.process_continue_exits(exits)
            if try_block.raise_from:
                self.process_raise_exits(exits)
            if try_block.return_from:
                self.process_return_exits(exits)
        return exits

    def _handle__TryExcept(self, node):
        # Python 2.7 uses separate TryExcept and TryFinally nodes. If we get
        # TryExcept, it means there was no finally, so fake it, and treat as
        # a general Try node.
        node.finalbody = []
        return self._handle__Try(node)

    def _handle__TryFinally(self, node):
        # Python 2.7 uses separate TryExcept and TryFinally nodes. If we get
        # TryFinally, see if there's a TryExcept nested inside. If so, merge
        # them. Otherwise, fake fields to complete a Try node.
        node.handlers = []
        node.orelse = []

        first = node.body[0]
        if first.__class__.__name__ == "TryExcept" and node.lineno == first.lineno:
            assert len(node.body) == 1
            node.body = first.body
            node.handlers = first.handlers
            node.orelse = first.orelse

        return self._handle__Try(node)

    def _handle__While(self, node):
        constant_test = self.is_constant_expr(node.test)
        start = to_top = self.line_for_node(node.test)
        if constant_test:
            to_top = self.line_for_node(node.body[0])
        self.block_stack.append(LoopBlock(start=start))
        exits = self.add_body_arcs(node.body, from_line=start)
        for xit in exits:
            self.arcs.add((xit, to_top))
        exits = set()
        my_block = self.block_stack.pop()
        exits.update(my_block.break_exits)
        if node.orelse:
            else_exits = self.add_body_arcs(node.orelse, from_line=start)
            exits |= else_exits
        else:
            # No `else` clause: you can exit from the start.
            if not constant_test:
                exits.add(start)
        return exits

    def _handle__With(self, node):
        start = self.line_for_node(node)
        exits = self.add_body_arcs(node.body, from_line=start)
        return exits

    _handle__AsyncWith = _handle__With

    def add_arcs_for_code_objects(self, root_node):
        for node in ast.walk(root_node):
            node_name = node.__class__.__name__
            code_object_handler = getattr(self, "_code_object__" + node_name, None)
            if code_object_handler is not None:
                code_object_handler(node)

    def _code_object__Module(self, node):
        start = self.line_for_node(node)
        if node.body:
            exits = self.add_body_arcs(node.body, from_line=-1)
            for xit in exits:
                self.arcs.add((xit, -start))
        else:
            # Empty module.
            self.arcs.add((-1, start))
            self.arcs.add((start, -1))

    def _code_object__FunctionDef(self, node):
        start = self.line_for_node(node)
        self.block_stack.append(FunctionBlock(start=start))
        exits = self.add_body_arcs(node.body, from_line=-1)
        self.block_stack.pop()
        for xit in exits:
            self.arcs.add((xit, -start))

    _code_object__AsyncFunctionDef = _code_object__FunctionDef

    def _code_object__ClassDef(self, node):
        start = self.line_for_node(node)
        self.arcs.add((-1, start))
        exits = self.add_body_arcs(node.body, from_line=start)
        for xit in exits:
            self.arcs.add((xit, -start))

    def do_code_object_comprehension(self, node):
        start = self.line_for_node(node)
        self.arcs.add((-1, start))
        self.arcs.add((start, -start))

    _code_object__GeneratorExp = do_code_object_comprehension
    _code_object__DictComp = do_code_object_comprehension
    _code_object__SetComp = do_code_object_comprehension
    if env.PY3:
        _code_object__ListComp = do_code_object_comprehension

    def _code_object__Lambda(self, node):
        start = self.line_for_node(node)
        self.arcs.add((-1, start))
        self.arcs.add((start, -start))
        # TODO: test multi-line lambdas

    def contains_return_expression(self, node):
        """Is there a yield-from or await in `node` someplace?"""
        for child in ast.walk(node):
            if child.__class__.__name__ in ["YieldFrom", "Await"]:
                return True

        return False


## Opcodes that guide the ByteParser.

def _opcode(name):
    """Return the opcode by name from the dis module."""
    return dis.opmap[name]


def _opcode_set(*names):
    """Return a set of opcodes by the names in `names`."""
    s = set()
    for name in names:
        try:
            s.add(_opcode(name))
        except KeyError:
            pass
    return s

# Opcodes that leave the code object.
OPS_CODE_END = _opcode_set('RETURN_VALUE')

# Opcodes that unconditionally end the code chunk.
OPS_CHUNK_END = _opcode_set(
    'JUMP_ABSOLUTE', 'JUMP_FORWARD', 'RETURN_VALUE', 'RAISE_VARARGS',
    'BREAK_LOOP', 'CONTINUE_LOOP',
)

# Opcodes that unconditionally begin a new code chunk.  By starting new chunks
# with unconditional jump instructions, we neatly deal with jumps to jumps
# properly.
OPS_CHUNK_BEGIN = _opcode_set('JUMP_ABSOLUTE', 'JUMP_FORWARD')

# Opcodes that push a block on the block stack.
OPS_PUSH_BLOCK = _opcode_set(
    'SETUP_LOOP', 'SETUP_EXCEPT', 'SETUP_FINALLY', 'SETUP_WITH', 'SETUP_ASYNC_WITH',
)

# Block types for exception handling.
OPS_EXCEPT_BLOCKS = _opcode_set('SETUP_EXCEPT', 'SETUP_FINALLY')

# Opcodes that pop a block from the block stack.
OPS_POP_BLOCK = _opcode_set('POP_BLOCK')

OPS_GET_AITER = _opcode_set('GET_AITER')

# Opcodes that have a jump destination, but aren't really a jump.
OPS_NO_JUMP = OPS_PUSH_BLOCK

# Individual opcodes we need below.
OP_BREAK_LOOP = _opcode('BREAK_LOOP')
OP_END_FINALLY = _opcode('END_FINALLY')
OP_COMPARE_OP = _opcode('COMPARE_OP')
COMPARE_EXCEPTION = 10  # just have to get this constant from the code.
OP_LOAD_CONST = _opcode('LOAD_CONST')
OP_RETURN_VALUE = _opcode('RETURN_VALUE')


class ByteParser(object):
    """Parse byte codes to understand the structure of code."""

    @contract(text='unicode')
    def __init__(self, text, code=None, filename=None):
        self.text = text
        if code:
            self.code = code
        else:
            try:
                self.code = compile_unicode(text, filename, "exec")
            except SyntaxError as synerr:
                raise NotPython(
                    u"Couldn't parse '%s' as Python source: '%s' at line %d" % (
                        filename, synerr.msg, synerr.lineno
                    )
                )

        # Alternative Python implementations don't always provide all the
        # attributes on code objects that we need to do the analysis.
        for attr in ['co_lnotab', 'co_firstlineno', 'co_consts', 'co_code']:
            if not hasattr(self.code, attr):
                raise CoverageException(
                    "This implementation of Python doesn't support code analysis.\n"
                    "Run coverage.py under CPython for this command."
                )

    def child_parsers(self):
        """Iterate over all the code objects nested within this one.

        The iteration includes `self` as its first value.

        """
        children = CodeObjects(self.code)
        return (ByteParser(self.text, code=c) for c in children)

    def _bytes_lines(self):
        """Map byte offsets to line numbers in `code`.

        Uses co_lnotab described in Python/compile.c to map byte offsets to
        line numbers.  Produces a sequence: (b0, l0), (b1, l1), ...

        Only byte offsets that correspond to line numbers are included in the
        results.

        """
        # Adapted from dis.py in the standard library.
        byte_increments = bytes_to_ints(self.code.co_lnotab[0::2])
        line_increments = bytes_to_ints(self.code.co_lnotab[1::2])

        last_line_num = None
        line_num = self.code.co_firstlineno
        byte_num = 0
        for byte_incr, line_incr in zip(byte_increments, line_increments):
            if byte_incr:
                if line_num != last_line_num:
                    yield (byte_num, line_num)
                    last_line_num = line_num
                byte_num += byte_incr
            line_num += line_incr
        if line_num != last_line_num:
            yield (byte_num, line_num)

    def _find_statements(self):
        """Find the statements in `self.code`.

        Produce a sequence of line numbers that start statements.  Recurses
        into all code objects reachable from `self.code`.

        """
        for bp in self.child_parsers():
            # Get all of the lineno information from this code.
            for _, l in bp._bytes_lines():
                yield l

    def _block_stack_repr(self, block_stack):               # pragma: debugging
        """Get a string version of `block_stack`, for debugging."""
        blocks = ", ".join(
            "(%s, %r)" % (dis.opname[b[0]], b[1]) for b in block_stack
        )
        return "[" + blocks + "]"

    def _split_into_chunks(self):
        """Split the code object into a list of `Chunk` objects.

        Each chunk is only entered at its first instruction, though there can
        be many exits from a chunk.

        Returns a list of `Chunk` objects.

        """
        # The list of chunks so far, and the one we're working on.
        chunks = []
        chunk = None

        # A dict mapping byte offsets of line starts to the line numbers.
        bytes_lines_map = dict(self._bytes_lines())

        # The block stack: loops and try blocks get pushed here for the
        # implicit jumps that can occur.
        # Each entry is a tuple: (block type, destination)
        block_stack = []

        # Some op codes are followed by branches that should be ignored.  This
        # is a count of how many ignores are left.
        ignore_branch = 0

        ignore_pop_block = 0

        # We have to handle the last two bytecodes specially.
        ult = penult = None

        # Get a set of all of the jump-to points.
        jump_to = set()
        bytecodes = list(ByteCodes(self.code.co_code))
        for bc in bytecodes:
            if bc.jump_to >= 0:
                jump_to.add(bc.jump_to)

        chunk_lineno = 0

        # Walk the byte codes building chunks.
        for bc in bytecodes:
            # Maybe have to start a new chunk.
            start_new_chunk = False
            first_chunk = False
            if bc.offset in bytes_lines_map:
                # Start a new chunk for each source line number.
                start_new_chunk = True
                chunk_lineno = bytes_lines_map[bc.offset]
                first_chunk = True
            elif bc.offset in jump_to:
                # To make chunks have a single entrance, we have to make a new
                # chunk when we get to a place some bytecode jumps to.
                start_new_chunk = True
            elif bc.op in OPS_CHUNK_BEGIN:
                # Jumps deserve their own unnumbered chunk.  This fixes
                # problems with jumps to jumps getting confused.
                start_new_chunk = True

            if not chunk or start_new_chunk:
                if chunk:
                    chunk.exits.add(bc.offset)
                chunk = Chunk(bc.offset, chunk_lineno, first_chunk)
                if not chunks:
                    # The very first chunk of a code object is always an
                    # entrance.
                    chunk.entrance = True
                chunks.append(chunk)

            # Look at the opcode.
            if bc.jump_to >= 0 and bc.op not in OPS_NO_JUMP:
                if ignore_branch:
                    # Someone earlier wanted us to ignore this branch.
                    ignore_branch -= 1
                else:
                    # The opcode has a jump, it's an exit for this chunk.
                    chunk.exits.add(bc.jump_to)

            if bc.op in OPS_CODE_END:
                # The opcode can exit the code object.
                chunk.exits.add(-self.code.co_firstlineno)
            if bc.op in OPS_PUSH_BLOCK:
                # The opcode adds a block to the block_stack.
                block_stack.append((bc.op, bc.jump_to))
            if bc.op in OPS_POP_BLOCK:
                # The opcode pops a block from the block stack.
                if ignore_pop_block:
                    ignore_pop_block -= 1
                else:
                    block_stack.pop()
            if bc.op in OPS_CHUNK_END:
                # This opcode forces the end of the chunk.
                if bc.op == OP_BREAK_LOOP:
                    # A break is implicit: jump where the top of the
                    # block_stack points.
                    chunk.exits.add(block_stack[-1][1])
                chunk = None
            if bc.op == OP_END_FINALLY:
                # For the finally clause we need to find the closest exception
                # block, and use its jump target as an exit.
                for block in reversed(block_stack):
                    if block[0] in OPS_EXCEPT_BLOCKS:
                        chunk.exits.add(block[1])
                        break
            if bc.op == OP_COMPARE_OP and bc.arg == COMPARE_EXCEPTION:
                # This is an except clause.  We want to overlook the next
                # branch, so that except's don't count as branches.
                ignore_branch += 1

            if bc.op in OPS_GET_AITER:
                # GET_AITER is weird: First, it seems to generate one more
                # POP_BLOCK than SETUP_*, so we have to prepare to ignore one
                # of the POP_BLOCKS.  Second, we don't have a clear branch to
                # the exit of the loop, so we peek into the block stack to find
                # it.
                ignore_pop_block += 1
                chunk.exits.add(block_stack[-1][1])

            penult = ult
            ult = bc

        if chunks:
            # The last two bytecodes could be a dummy "return None" that
            # shouldn't be counted as real code. Every Python code object seems
            # to end with a return, and a "return None" is inserted if there
            # isn't an explicit return in the source.
            if ult and penult:
                if penult.op == OP_LOAD_CONST and ult.op == OP_RETURN_VALUE:
                    if self.code.co_consts[penult.arg] is None:
                        # This is "return None", but is it dummy?  A real line
                        # would be a last chunk all by itself.
                        if chunks[-1].byte != penult.offset:
                            ex = -self.code.co_firstlineno
                            # Split the last chunk
                            last_chunk = chunks[-1]
                            last_chunk.exits.remove(ex)
                            last_chunk.exits.add(penult.offset)
                            chunk = Chunk(
                                penult.offset, last_chunk.line, False
                            )
                            chunk.exits.add(ex)
                            chunks.append(chunk)

            # Give all the chunks a length.
            chunks[-1].length = bc.next_offset - chunks[-1].byte
            for i in range(len(chunks)-1):
                chunks[i].length = chunks[i+1].byte - chunks[i].byte

        #self.validate_chunks(chunks)
        return chunks

    def validate_chunks(self, chunks):                      # pragma: debugging
        """Validate the rule that chunks have a single entrance."""
        # starts is the entrances to the chunks
        starts = set(ch.byte for ch in chunks)
        for ch in chunks:
            assert all((ex in starts or ex < 0) for ex in ch.exits)

    def _arcs(self):
        """Find the executable arcs in the code.

        Yields pairs: (from,to).  From and to are integer line numbers.  If
        from is < 0, then the arc is an entrance into the code object.  If to
        is < 0, the arc is an exit from the code object.

        """
        chunks = self._split_into_chunks()

        # A map from byte offsets to the chunk starting at that offset.
        byte_chunks = dict((c.byte, c) for c in chunks)

        # Traverse from the first chunk in each line, and yield arcs where
        # the trace function will be invoked.
        for chunk in chunks:
            if chunk.entrance:
                yield (-1, chunk.line)

            if not chunk.first:
                continue

            chunks_considered = set()
            chunks_to_consider = [chunk]
            while chunks_to_consider:
                # Get the chunk we're considering, and make sure we don't
                # consider it again.
                this_chunk = chunks_to_consider.pop()
                chunks_considered.add(this_chunk)

                # For each exit, add the line number if the trace function
                # would be triggered, or add the chunk to those being
                # considered if not.
                for ex in this_chunk.exits:
                    if ex < 0:
                        yield (chunk.line, ex)
                    else:
                        next_chunk = byte_chunks[ex]
                        if next_chunk in chunks_considered:
                            continue

                        # The trace function is invoked if visiting the first
                        # bytecode in a line, or if the transition is a
                        # backward jump.
                        backward_jump = next_chunk.byte < this_chunk.byte
                        if next_chunk.first or backward_jump:
                            if next_chunk.line != chunk.line:
                                yield (chunk.line, next_chunk.line)
                        else:
                            chunks_to_consider.append(next_chunk)

    def _all_chunks(self):
        """Returns a list of `Chunk` objects for this code and its children.

        See `_split_into_chunks` for details.

        """
        chunks = []
        for bp in self.child_parsers():
            chunks.extend(bp._split_into_chunks())

        return chunks

    def _all_arcs(self):
        """Get the set of all arcs in this code object and its children.

        See `_arcs` for details.

        """
        arcs = set()
        for bp in self.child_parsers():
            arcs.update(bp._arcs())

        return arcs


class Chunk(object):
    """A sequence of byte codes with a single entrance.

    To analyze byte code, we have to divide it into chunks, sequences of byte
    codes such that each chunk has only one entrance, the first instruction in
    the block.

    This is almost the CS concept of `basic block`_, except that we're willing
    to have many exits from a chunk, and "basic block" is a more cumbersome
    term.

    .. _basic block: http://en.wikipedia.org/wiki/Basic_block

    `byte` is the offset to the bytecode starting this chunk.

    `line` is the source line number containing this chunk.

    `first` is true if this is the first chunk in the source line.

    An exit < 0 means the chunk can leave the code (return).  The exit is
    the negative of the starting line number of the code block.

    The `entrance` attribute is a boolean indicating whether the code object
    can be entered at this chunk.

    """
    def __init__(self, byte, line, first):
        self.byte = byte
        self.line = line
        self.first = first
        self.length = 0
        self.entrance = False
        self.exits = set()

    def __repr__(self):
        return "<%d+%d @%d%s%s %r>" % (
            self.byte,
            self.length,
            self.line,
            "!" if self.first else "",
            "v" if self.entrance else "",
            list(self.exits),
        )


SKIP_DUMP_FIELDS = ["ctx"]

def is_simple_value(value):
    return (
        value in [None, [], (), {}, set()] or
        isinstance(value, (string_class, int, float))
    )

def ast_dump(node, depth=0):
    indent = " " * depth
    if not isinstance(node, ast.AST):
        print("{0}<{1} {2!r}>".format(indent, node.__class__.__name__, node))
        return

    lineno = getattr(node, "lineno", None)
    if lineno is not None:
        linemark = " @ {0}".format(node.lineno)
    else:
        linemark = ""
    head = "{0}<{1}{2}".format(indent, node.__class__.__name__, linemark)

    named_fields = [
        (name, value)
        for name, value in ast.iter_fields(node)
        if name not in SKIP_DUMP_FIELDS
    ]
    if not named_fields:
        print("{0}>".format(head))
    elif len(named_fields) == 1 and is_simple_value(named_fields[0][1]):
        field_name, value = named_fields[0]
        print("{0} {1}: {2!r}>".format(head, field_name, value))
    else:
        print(head)
        if 0:
            print("{0}# mro: {1}".format(
                indent, ", ".join(c.__name__ for c in node.__class__.__mro__[1:]),
            ))
        next_indent = indent + "    "
        for field_name, value in named_fields:
            prefix = "{0}{1}:".format(next_indent, field_name)
            if is_simple_value(value):
                print("{0} {1!r}".format(prefix, value))
            elif isinstance(value, list):
                print("{0} [".format(prefix))
                for n in value:
                    ast_dump(n, depth + 8)
                print("{0}]".format(next_indent))
            else:
                print(prefix)
                ast_dump(value, depth + 8)

        print("{0}>".format(indent))
