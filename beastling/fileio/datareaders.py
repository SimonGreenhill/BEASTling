import csv
import sys
import collections

from clldutils.dsv import UnicodeDictReader


def load_data(filename, file_format=None, lang_column=None):
    # Handle CSV dialect issues
    if filename == 'stdin':
        filename = sys.stdin
        # We can't sniff from stdin, so guess comma-delimited and hope for
        # the best
        dialect = "excel" # Default dialect for csv module
    elif file_format and file_format.lower() == "cldf":
        # CLDF standard says delimiter is indicated by file extension
        if str(filename).lower().endswith("csv") or filename == "stdin":
            dialect = "excel"
        elif str(filename).lower().endswith("tsv"):
            dialect = "excel_tab"
        else:
            raise ValueError("CLDF standard dictates that filenames must end in .csv or .tsv")
    else:
        # Use CSV dialect sniffer in all other cases
        fp = open(str(filename), "r") # Cast PosixPath to str
        # On large files, csv.Sniffer seems to need a lot of datta to make a
        # successful inference...
        sample = fp.read(1024)
        while True:
            try:
                dialect = csv.Sniffer().sniff(sample)
                break
            except csv.Error:
                sample += fp.read(1024)
        fp.close()

    # Read
    with UnicodeDictReader(filename, dialect=dialect) as reader:
        # Guesstimate file format if user has not been explicit
        if file_format is None:
            file_format = 'cldf' if all(
                [f in reader.fieldnames for f in ("Language_ID", "Value")]) and any(
                    [f in reader.fieldnames for f in ("Feature_ID", "Parameter_ID")]
                ) else 'beastling'

        # Load data
        if file_format == 'cldf':
            data = load_cldf_data(reader)
        elif file_format == 'beastling':
            data = load_beastling_data(reader, lang_column, filename)
        else:
            raise ValueError("File format specification '{:}' not understood".format(file_format))
    return data

_language_column_names = ("iso", "iso_code", "glotto", "glottocode", "language", "language_id", "lang", "lang_id")


def load_beastling_data(reader, lang_column, filename):
    if not lang_column:
        for candidate in reader.fieldnames:
            if candidate.lower() in _language_column_names:
                lang_column = candidate
                break

    if not lang_column or lang_column not in reader.fieldnames:
        raise ValueError("Cold not find language column in data file %s" % filename)
    data = collections.defaultdict(lambda: collections.defaultdict(lambda: "?"))
    for row in reader:
        if row[lang_column] in data:
            raise ValueError("Duplicated language identifier '%s' found in data file %s" % (row[lang_column], filename))
        data[row[lang_column]] = collections.defaultdict(lambda : "?", row)
    return data


def load_cldf_data(reader):
    if "Feature_ID" in reader.fieldnames:
        feature_column = "Feature_ID"
    else:
        feature_column = "Parameter_ID"
    data = collections.defaultdict(lambda: collections.defaultdict(lambda: "?"))
    for row in reader:
        lang = row["Language_ID"]
        if lang not in data:
            data[lang] = collections.defaultdict(lambda :"?")
        data[lang][row[feature_column]] = row["Value"]
    return data
