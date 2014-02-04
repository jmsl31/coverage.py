relative_path = None

def html_it():
    """Run coverage and make an XML report for a."""
    import coverage
    cov = coverage.coverage()
    cov.start()
    import a            # pragma: nested
    cov.stop()          # pragma: nested
    cov.xml_report(a, outfile="../xml_1/coverage.xml")
    global relative_path
    relative_path = cov.file_locator.relative_dir.rstrip('/')

import os
if not os.path.exists("xml_1"):
    os.makedirs("xml_1")

runfunc(html_it, rundir="src")

compare("gold_x_xml", "xml_1", scrubs=[
    (r' timestamp="\d+"', ' timestamp="TIMESTAMP"'),
    (r' version="[-.\w]+"', ' version="VERSION"'),
    (r'<source></source>', '<source>%s</source>' % relative_path),
    (r'/code/coverage/?[-.\w]*', '/code/coverage/VER'),
    ])
clean("xml_1")
