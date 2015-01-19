"""XML reporting for coverage.py"""

import os
import sys
import time
import xml.dom.minidom

from coverage import __url__, __version__
from coverage.report import Reporter


def rate(hit, num):
    """Return the fraction of `hit`/`num`, as a string."""
    if num == 0:
        return "1"
    else:
        return "%.4g" % (float(hit) / num)


class XmlReporter(Reporter):
    """A reporter for writing Cobertura-style XML coverage results."""

    def __init__(self, coverage, config):
        super(XmlReporter, self).__init__(coverage, config)

        self.source_paths = set()
        self.packages = {}
        self.xml_out = None
        self.arcs = coverage.data.has_arcs()

    def report(self, morfs, outfile=None):
        """Generate a Cobertura-compatible XML report for `morfs`.

        `morfs` is a list of modules or filenames.

        `outfile` is a file object to write the XML to.

        """
        # Initial setup.
        outfile = outfile or sys.stdout

        # Create the DOM that will store the data.
        impl = xml.dom.minidom.getDOMImplementation()
        docType = impl.createDocumentType(
            "coverage", None,
            "http://cobertura.sourceforge.net/xml/coverage-03.dtd"
            )
        self.xml_out = impl.createDocument(None, "coverage", docType)

        # Write header stuff.
        xcoverage = self.xml_out.documentElement
        xcoverage.setAttribute("version", __version__)
        xcoverage.setAttribute("timestamp", str(int(time.time()*1000)))
        xcoverage.appendChild(self.xml_out.createComment(
            " Generated by coverage.py: %s " % __url__
            ))

        # Call xml_file for each file in the data.
        self.report_files(self.xml_file, morfs)

        xsources = self.xml_out.createElement("sources")
        xcoverage.appendChild(xsources)

        # Populate the XML DOM with the source info.
        for path in sorted(self.source_paths):
            xsource = self.xml_out.createElement("source")
            xsources.appendChild(xsource)
            txt = self.xml_out.createTextNode(path)
            xsource.appendChild(txt)

        lnum_tot, lhits_tot = 0, 0
        bnum_tot, bhits_tot = 0, 0

        xpackages = self.xml_out.createElement("packages")
        xcoverage.appendChild(xpackages)

        # Populate the XML DOM with the package info.
        for pkg_name in sorted(self.packages.keys()):
            pkg_data = self.packages[pkg_name]
            class_elts, lhits, lnum, bhits, bnum = pkg_data
            xpackage = self.xml_out.createElement("package")
            xpackages.appendChild(xpackage)
            xclasses = self.xml_out.createElement("classes")
            xpackage.appendChild(xclasses)
            for class_name in sorted(class_elts.keys()):
                xclasses.appendChild(class_elts[class_name])
            xpackage.setAttribute("name", pkg_name.replace(os.sep, '.'))
            xpackage.setAttribute("line-rate", rate(lhits, lnum))
            if self.arcs:
                branch_rate = rate(bhits, bnum)
            else:
                branch_rate = "0"
            xpackage.setAttribute("branch-rate", branch_rate)
            xpackage.setAttribute("complexity", "0")

            lnum_tot += lnum
            lhits_tot += lhits
            bnum_tot += bnum
            bhits_tot += bhits

        xcoverage.setAttribute("line-rate", rate(lhits_tot, lnum_tot))
        if self.arcs:
            branch_rate = rate(bhits_tot, bnum_tot)
        else:
            branch_rate = "0"
        xcoverage.setAttribute("branch-rate", branch_rate)

        # Use the DOM to write the output file.
        outfile.write(self.xml_out.toprettyxml())

        # Return the total percentage.
        denom = lnum_tot + bnum_tot
        if denom == 0:
            pct = 0.0
        else:
            pct = 100.0 * (lhits_tot + bhits_tot) / denom
        return pct

    def xml_file(self, cu, analysis):
        """Add to the XML report for a single file."""

        # Create the 'lines' and 'package' XML elements, which
        # are populated later.  Note that a package == a directory.
        filename = cu.file_locator.relative_filename(cu.filename)
        filename = filename.replace("\\", "/")
        dirname = os.path.dirname(filename) or "."
        package_name = dirname.replace("/", ".")
        className = cu.name

        self.source_paths.add(cu.file_locator.relative_dir.rstrip('/'))
        package = self.packages.setdefault(package_name, [{}, 0, 0, 0, 0])

        xclass = self.xml_out.createElement("class")

        xclass.appendChild(self.xml_out.createElement("methods"))

        xlines = self.xml_out.createElement("lines")
        xclass.appendChild(xlines)

        xclass.setAttribute("name", os.path.relpath(filename, dirname))
        xclass.setAttribute("filename", filename)
        xclass.setAttribute("complexity", "0")

        branch_stats = analysis.branch_stats()

        # For each statement, create an XML 'line' element.
        for line in sorted(analysis.statements):
            xline = self.xml_out.createElement("line")
            xline.setAttribute("number", str(line))

            # Q: can we get info about the number of times a statement is
            # executed?  If so, that should be recorded here.
            xline.setAttribute("hits", str(int(line not in analysis.missing)))

            if self.arcs:
                if line in branch_stats:
                    total, taken = branch_stats[line]
                    xline.setAttribute("branch", "true")
                    xline.setAttribute(
                        "condition-coverage",
                        "%d%% (%d/%d)" % (100*taken/total, taken, total)
                        )
            xlines.appendChild(xline)

        class_lines = len(analysis.statements)
        class_hits = class_lines - len(analysis.missing)

        if self.arcs:
            class_branches = sum(t for t, k in branch_stats.values())
            missing_branches = sum(t - k for t, k in branch_stats.values())
            class_br_hits = class_branches - missing_branches
        else:
            class_branches = 0.0
            class_br_hits = 0.0

        # Finalize the statistics that are collected in the XML DOM.
        xclass.setAttribute("line-rate", rate(class_hits, class_lines))
        if self.arcs:
            branch_rate = rate(class_br_hits, class_branches)
        else:
            branch_rate = "0"
        xclass.setAttribute("branch-rate", branch_rate)

        package[0][className] = xclass
        package[1] += class_hits
        package[2] += class_lines
        package[3] += class_br_hits
        package[4] += class_branches
