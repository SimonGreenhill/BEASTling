import argparse
import sys
import traceback
import pathlib
import logging

from beastling import __version__
from beastling.beastxml import BeastXml
from beastling.configuration import Configuration
from beastling.extractor import extract
from beastling.report import BeastlingReport
from beastling.report import BeastlingGeoJSON

wrap_errors = Exception


def exit(msg=None, status=0, exception=False):
    if msg:
        sys.stderr.write(msg + '\n')
    if exception:
        traceback.print_exc()
    sys.exit(status)


def main(*args):

    # Parse command line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "config",
        help="Beastling configuration file(s) (or XML file if --extract is used)",
        type=pathlib.Path,
        default=None,
        nargs="+")
    parser.add_argument(
        "--extract",
        default=False,
        action="store_true",
        help="Extract configuration file (and possibly data files) from a BEASTling-generated XML file.")
    parser.add_argument(
        "--report",
        default=False,
        action="store_true",
        help="Save a high-level report on the analysis as a Markdown file.")
    parser.add_argument(
        "--language-list",
        default=False,
        action="store_true",
        help="Save a list of languages in the analysis as a plain text file.")
    parser.add_argument(
        "-o", "--output",
        help="Output filename, for example `-o analysis.xml`",
        default=None)
    parser.add_argument(
        "--overwrite",
        help="Overwrite an existing configuration file.",
        default=False,
        action="store_true")
    parser.add_argument(
        "--stdin",
        help="Read data from stdin.",
        default=False,
        action="store_true")
    parser.add_argument(
        "--prior", "--sample-from-prior", "-p",
        help="Generate XML file which samples from the prior, not posterior.",
        default=False,
        action="store_true")
    parser.add_argument(
        "-v", "--verbose",
        help="Display details of the generated analysis.",
        default=False,
        action="store_true")
    parser.add_argument(
        "--version",
        action="version",
        version = "BEASTling %s" % __version__)
    args = parser.parse_args(args or None)
    if args.verbose:
        # set the log level:
        logging.basicConfig()
        logging.getLogger().setLevel(logging.INFO)
    if args.extract:
        do_extract(args)
    else:
        do_generate(args)
    exit(status=0)


def do_extract(args):
    if len(args.config) != 1:
        exit(msg="Can only extract from exactly one BEAST XML file", status=1)
    if not args.config[0].exists():
        exit(msg="No such BEAST XML file: %s" % args.config, status=2)
    try:
        for msg in extract(args.config[0], args.overwrite):
            sys.stdout.write(msg)
    except wrap_errors as e:
        exit(
            msg="Error encountered while extracting BEASTling config and/or data files:",
            status=3,
            exception=True)


def do_generate(args):

    # Make sure the requested configuration file exists
    for conf in args.config:
        if not conf.exists():
            exit(msg="No such configuration file: %s" % conf, status=1)

    # Build but DON'T PROCESS the Config object
    # This is fast, and gives us enough information to check whether or not
    try:
        config = Configuration(
            configfile=args.config, stdin_data=args.stdin, prior=args.prior, force_glottolog_load=args.report)
    except wrap_errors as e: # PRAGMA: NO COVER
        exit(msg="Error encountered while parsing configuration file:", status=2, exception=True)

    # Make sure we can write to the appropriate output filename
    output_filename = pathlib.Path(args.output) if args.output else config.admin.path(".xml")
    if output_filename.exists() and not args.overwrite:
        exit(msg="File %s already exists! Run beastling with the --overwrite option if you wish "
                 "to overwrite it." % output_filename,
             status=4)

    # Now that we know we will be able to save the resulting XML, we can take
    # the time to process the config object
    try:
        config.process()
    except wrap_errors as e:  # pragma: no cover
        exit(msg="Error encountered while parsing configuration file:", status=2, exception=True)

    # Build XML file
    try:
        xml = BeastXml(config)
    except wrap_errors as e:  # pragma: no cover
        exit(msg="Error encountered while building BeastXML object:", status=3, exception=True)

    # Write XML file
    xml.write_file(output_filename)

    # Build and write report
    if args.report:
        report = BeastlingReport(config)
        report.write_file(output_filename.parent / config.admin.path(".md"))
        geojson = BeastlingGeoJSON(config)
        geojson.write_file(output_filename.parent / config.admin.path(".geojson"))

    # Build and write language list
    if args.language_list:
        write_language_list(config)


def write_language_list(config):
    config.admin.path("_languages.txt").write_text("\n".join(config.languages.languages)+"\n", encoding='utf8')
