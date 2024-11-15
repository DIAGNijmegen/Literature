import io
import os.path
import csv
import glob

import numpy as np
import pyarxiv
import requests
import dropbox
import datetime
from unidecode import unidecode
from collections import Counter
import re
import tqdm
import tqdm.auto
from pathlib import Path
import colors

from pdf2image import convert_from_path

from pdf2image.exceptions import (
    PDFInfoNotInstalledError,
    PDFPageCountError,
    PDFSyntaxError
)

# this file contains functionality to read diag.bib or another bib file and do all sorts of checks and processing, like
# provide overall statistics
# check if journal name strings exist and are correctly redined to full and abbreviated names
# check if journal names are only referred to by journal name strings
# find double capitals in title that need a {}
# entries like Vree16 have abstracts on multiple lines, they are not formatted correctly
# check if key matches year
# check format of key
# check if pdf name matches key
# check if pdf is listed and exists on disc
# extract first page of pdf to see if it is the correct version
# copy diag pdfs to dropbox location, get temp links https://www.dropboxforum.com/t5/API-Support-Feedback/Generate-links-and-passwords-with-Python/td-p/198399
# remove trailing point in title
# add {} around capitalized abbreviations in title
# check if arxiv links are correct
# check if doi is present when it should be and if it resolves
# check if pmids are correct
# retrieve citations via google scholar, using publish or perish lists

allowed_fields = frozenset(
    ['author', 'title', 'journal', 'year', 'volume', 'issue', 'month', 'pages', 'doi', 'abstract', 'file',
     'optnote', 'pmid', 'gsid', 'gscites', 'booktitle', 'school', 'number', 'url', 'copromotor', 'promotor',
     'publisher', 'series', 'algorithm', 'code', 'taverne_url', 'ss_id', 'all_ss_ids', 'automatic', 'citation-count'])


def recode(chars):
    for possible_encoding in ("latin_1", "windows-1252"):
        try:
            return chars.encode(possible_encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    raise ValueError(f"Could not recode '{chars}'")


def select_existing_path(*ps):
    return [p for p in ps if os.path.exists(p)].pop()


# automatically select an existing path, allows functions to run on machines with different roots for literature
literature_root = select_existing_path(
    r'C:\svn_diag',
    r'C:\git\literature',
    '.'
    # add other possible paths here
)


def strip_cb(s):
    '''removes curly braces and white space at begin and end'''
    s = s.strip()
    while len(s) > 0 and s[0] == "{":
        s = s[1:len(s)]
        s = s.strip()
    while len(s) > 0 and s[-1] == "}":
        s = s[0:len(s) - 1]
        s = s.strip()
    return s


def onlyletters(s):
    t = ""
    for char in s:
        if char.isalpha():
            t += char
    return t


def split_strip(s, sep=[',']):
    '''splits the string, removing trailing spaces around every element'''
    s = s.strip()
    t = []
    p = s.find(sep[0])
    while p > -1:
        t.append(s[0:p].strip())
        s = s[p + 1:len(s)]
        p = s.find(sep[0])
    if len(s) > 0:
        t.append(s.strip())
    return t


class BibEntry:

    def __init__(self):
        self.key = ""
        self.type = ""
        self.string = ""
        self.value = ""
        self.pdf = False
        self.line = ""
        self.fields = {}

    def to_lines(self):
        strings = []
        if self.type == "string":
            strings.append(f'@' + self.type + '{' + self.key + " = " + self.value + '}\n')
        elif self.type == "comment":
            pass
        else:
            strings.append('@' + self.type + '{' + self.key + ",\n")
            for k, v in self.fields.items():
                if k in allowed_fields:
                    value = unidecode(v)
                    strings.append('  ' + k + " = " + value + ",\n")
            strings.append('}\n')
        return strings

    def reformat_optnote(self):
        if self.fields.get('optnote'):
            s = self.fields['optnote']
            s = strip_cb(s)
            s = split_strip(s)
            for i in s:
                i = i.strip()
                i = i.upper()
            s.sort()
            ss = "{"
            for i in s:
                ss += i + ", "
            ss = ss[:-2] + "}"
            self.fields['optnote'] = ss

    def isDIAG(self):
        if not self.fields.get('optnote'):
            return False
        s = self.fields['optnote']
        return s.find("DIAG") != -1

    def getFieldValue(self):
        """
        Eats one field and value from the long line making up the remainder of the bib entry
        """
        i = self.line.find("=")
        if (i < 0):
            return False
        field = self.line[0:i].strip()
        self.line = self.line[i + 1:len(self.line)]
        # do we find first a comma or first a curly brace
        comma = self.line.find(",")
        brace = self.line.find("{")
        if brace > -1 and comma > -1 and brace < comma:
            count = 1
            i = brace + 1
            while count > 0:
                if self.line[i] == "}":
                    count -= 1
                if self.line[i] == "{":
                    count += 1
                i += 1
            self.value = self.line[brace:i].strip()
            self.line = self.line[i + 1:len(self.line)].strip()
        elif comma > -1:
            self.value = self.line[0:comma].strip()
            self.line = self.line[comma + 1:len(self.line)].strip()
        else:
            assert False
        self.fields[field] = self.value
        return True

    def parse(self, lines):
        '''lines makes up all lines of a bib entry'''
        # first turn lines into one long string
        self.line = ''
        for i in range(0, len(lines)):
            self.line += lines[i]
            self.line += ' '
        self.line = self.line.strip()
        if (len(self.line) == 0):
            return

        # find type and key
        assert (self.line[0] == "@")
        i = self.line.find("{")
        assert (i > 1);
        self.type = self.line[1:i].strip().lower()

        # if type is string, we get the string key and value and we're done
        if (self.type == 'string'):
            j = self.line.find("=")
            assert (j > i);
            self.key = self.line[i + 1:j].strip()
            k = self.line.find("}")
            assert (k > j)
            self.value = self.line[j + 1:k].strip()
            return

        # if type is comment, we get the value and we're done
        if (self.type == 'comment'):
            j = self.line.find("}")
            assert (j > i);
            self.value = self.line[i + 1:j].strip()
            return

        # get the key
        j = self.line.find(",")
        assert (j > i)
        self.key = self.line[i + 1:j].strip()
        self.line = self.line[j + 1:len(self.line)].strip()

        assert (self.line[-1] == "}"), f"{self.line}"
        self.line = self.line[:-1] + ",}"  # possibly extra comma, makes sure there is one!

        # next we process the rest of the entry, field by field
        while self.getFieldValue():
            pass

    def check_pdf_exists(self, path):
        # warning: use hardcoded path here
        fn = os.path.join(path, self.key + '.pdf')
        self.pdf = os.path.isfile(fn)
        return self.pdf


def read_bibfile(filename, full_path=None):
    entries = []
    if full_path == None:
        fp = open(literature_root + '/' + filename, encoding='utf-8')
    else:
        fp = open(full_path, encoding='utf-8')
    line = fp.readline()
    while line and line.find("@") != 0:
        line = fp.readline()  # find first entry
    entry = []
    while line:
        if line.find("@") == 0:  # new entry found
            be = BibEntry()
            be.parse(entry)
            be.reformat_optnote()
            if len(be.key) > 0:
                entries.append(be)
            entry = [line]
        else:
            entry.append(line)
        line = fp.readline()
    # parse the last entry
    be = BibEntry()
    be.parse(entry)
    be.reformat_optnote()
    if len(be.key) > 0:
        entries.append(be)
    fp.close()
    return entries

def parse_bibtex_string(bibtex_string: str) -> BibEntry:
    # Create a new BibEntry instance
    bib_entry = BibEntry()

    # Remove leading/trailing spaces and ensure proper formatting
    bibtex_string = bibtex_string.strip()

    # Use regex to extract the BibTeX entry type (e.g., article) and key (e.g., Hopp24a)
    type_and_key_match = re.match(r"@(\w+)\{([^,]+),", bibtex_string)
    if not type_and_key_match:
        raise ValueError("Invalid BibTeX entry format")

    bib_entry.type = type_and_key_match.group(1).lower()  # Extract entry type
    bib_entry.key = type_and_key_match.group(2).strip()  # Extract entry key

    # Now we remove the entry type and key from the string, leaving just the fields
    fields_string = bibtex_string[type_and_key_match.end():].strip()

    # A recursive function to parse fields with nested braces
    def parse_fields(fields_str):
        fields = {}
        stack = []
        key, value, brace_level = None, "", 0

        for i, char in enumerate(fields_str):
            if char == "=" and brace_level == 0 and not key:
                key = fields_str[:i].strip()  # Capture the key
                stack.append("=")
            elif char == "{" and stack and stack[-1] == "=":
                brace_level += 1
                value += char
            elif char == "}":
                brace_level -= 1
                value += char
                if brace_level == 0:
                    fields[key] = value.strip()
                    stack.pop()  # Remove "="
                    remainder = fields_str[i + 1:].lstrip(", ")
                    if remainder:
                        fields.update(parse_fields(remainder))  # Parse the remaining fields
                    break
            elif brace_level > 0:
                value += char

        return fields

    # Extract individual fields, properly handling nested braces
    fields = parse_fields(fields_string)
    bib_entry.fields = {k.strip().lower(): v.strip() for k, v in fields.items()}

    return bib_entry


def statistics(e):
    print("\nStatistics on entries\n")
    kd = {}
    for i in range(0, len(e)):
        s = e[i].type
        if kd.get(s) == None:
            kd[s] = 1
        else:
            kd[s] = kd[s] + 1
    key_list = kd.keys()
    for key in key_list:
        # print the specific value for the key
        print('key = ' + key + ' value = ' + str(kd[key]))
    print("\nStatistics on fields within entries")
    kd = {}
    for i in range(0, len(e)):
        key_list = e[i].fields.keys()
        for s in key_list:
            if kd.get(s) == None:
                kd[s] = 1
            else:
                kd[s] = kd[s] + 1
    key_list = kd.keys()
    for key in key_list:
        # print the specific value for the key
        print('key = ' + key + ' value = ' + str(kd[key]))


# check if thumbnail (png image of first page of pdf) exists, if not create it
def create_thumb(pdfpath, thumbpath, key):
    pdfname = pdfpath + key + '.pdf'
    thumbname = thumbpath + key + '.png'
    if not os.path.isfile(pdfname):
        print("cannot find pdf file " + pdfname)
        return
    if os.path.isfile(thumbname):
        # thumb already exists, we're done
        return
    print("will create png for " + pdfname)
    images = convert_from_path(pdfname)
    if len(images) > 0:
        images[0].save(thumbname, "PNG")
        print("Wrote " + thumbname)


def check_missing_pdfs(e, addmissingthumbs):
    print("\nPrinting journal/conference article entries (not arXiv) with a missing pdf file:")
    for i in e:
        if i.type == 'article' or i.type == 'inproceedings':
            j = i.fields.get('journal')
            if i.type == 'article' and j == None:
                print(f"No journal field in journal article {i.key}")
            else:
                if j != None and i.type == 'article' and j.find("arXiv") == -1:
                    if i.check_pdf_exists(os.path.join(literature_root, 'pdf/')) == False:
                        print(f"Missing pdf for journal article {i.key}")
                    else:
                        if addmissingthumbs:
                            create_thumb(os.path.join(literature_root, 'pdf/'),
                                         os.path.join(literature_root, 'png', 'publications/'), i.key)
                elif i.type == 'inproceedings':
                    if i.check_pdf_exists(os.path.join(literature_root, 'pdf/')) == False:
                        print(f"Missing pdf for inproceedings {i.key}")
                    else:
                        if addmissingthumbs:
                            create_thumb(os.path.join(literature_root, 'pdf/'),
                                         os.path.join(literature_root, 'png', 'publications/'), i.key)


def read_pop():
    files = glob.glob(literature_root + "/pop/*.csv")
    gsdata = {}
    for f in files:
        print("Reading ", f)
        with open(f, encoding="utf-8") as csvfile:
            # dialect = csv.Sniffer().sniff(csvfile.read(1024))
            # csvfile.seek(0)
            reader = csv.reader(csvfile, 'excel')
            # reader = csv.DictReader(csvfile, None, None, None, dialect)
            linecount = 0
            n = 0
            for row in reader:
                if linecount > 0:
                    gsurl = row[7]
                    i = gsurl.find('&cites=')
                    gsid = ''
                    if (i > -1):
                        gsid = gsurl[i + 7:len(gsurl)]
                    author = row[1]
                    year = row[3]
                    title = row[2]
                    journal = row[4]
                    cites = int(row[0])
                    if cites > 0:
                        n += 1
                        gsdata[gsid] = [author, title, year, journal, cites]
                linecount += 1
            print(f"Processed {n} entries")
    print(f"Returning {len(gsdata)} Google Scholar items with at least 1 citation")
    return gsdata


def add_gsid(gsdata, entries):
    matches = {}
    for i in range(10):
        matches[i] = 0
    for gsid, v in gsdata.items():
        author = v[0]
        title = v[1]
        year = v[2]
        journal = v[3]
        cites = v[4]

        # get bibkey author part and year
        i = author.find(",")
        if i > -1:
            author = author[0:i]
        i = author.rfind(" ")
        if i > -1:
            author = author[i + 1:len(author)]
        author = author.lower()  # needs more checks
        author = unidecode(author)
        author = author[0:min(4, len(author))]
        a = author[0].upper()
        author = a + author[1:len(author)]
        year2 = year[2:4]
        bibkey = author  # do not add year, search any year...
        bibkey2 = author + year2

        # convert title to make matching easier
        title = onlyletters(title).lower()
        title = title[0:min(len(title), 80)]

        # now we find bibitems from entries that may match the gs item
        # we work with a point system
        candidates = []
        for k in entries:
            points = 0
            if k.key.find(bibkey) > -1:
                points = 1
                if k.key.find(bibkey2) > -1:
                    points += 1
            btitle = k.fields.get("title")
            if btitle is not None:
                btitle = onlyletters(btitle).lower()
                btitle = btitle[0:min(len(btitle), 80)]
                if btitle == title:
                    points += 4
            if points > 0:
                candidates.append([points, k.key])
        candidates.sort()
        candidates.reverse()
        if len(candidates) == 1:
            candidates[0][0] += 1
        if len(candidates) > 0:
            # print(cites, "-", gsid, "-", year, "-", bibkey, "-", title, "-", journal, " ", candidates)
            matches[candidates[0][0]] += 1
        else:
            print(cites, "-", gsid, "-", year, "-", bibkey, "-", title, "-", journal, " NO MATCH")
            matches[0] += 1

        # now we start to act based on what we found
        if len(candidates) > 0:
            key = candidates[0][1]
            points = candidates[0][0]
            for e in entries:
                if e.key == key:
                    if e.fields.get("gsid") is not None:
                        bibgsid = strip_cb(e.fields['gsid'])
                        if gsid != bibgsid and points > 5:
                            print(f"found mismatch in {key}: {bibgsid} vs {gsid}")
                            if gsdata.get(bibgsid) is not None:
                                print(gsdata[bibgsid])
                            else:
                                print("bibgsid not found")
                            print(gsdata[gsid])
                            print()
                    else:
                        if points > 5:
                            e.fields["gsid"] = "{" + gsid + "}"
                        elif points > 3:
                            print("Possible match:")
                            for j in v:
                                print(f"  {j}")
                            for j in e.to_lines():
                                print(j[0:-1])
                            print("Match these? [y/n]")
                            ans = input()
                            if ans == 'y':
                                print("Matching!")
                                e.fields["gsid"] = "{" + gsid + "}"
                        else:
                            print(cites, "-", gsid, "-", year, "-", bibkey, "-", title, "-", journal, " NO MATCH")

    print(matches)


def update_gscites(gsdata, entries):
    for k in entries:
        if k.fields.get("gsid") is not None:
            gsid = strip_cb(k.fields["gsid"])
            if gsdata.get(gsid) is None:
                print(f"Bib entry {k.key} contains google scholar id {gsid}, but this id is not in the pop files")
                continue
            cites = "{" + str(gsdata[gsid][4]) + "}"
            oldcites = k.fields.get("gscites", 0)
            if cites != oldcites:
                k.fields["gscites"] = cites
                print(f"{k.key} citations updated from {oldcites} to {cites}")
        else:
            if k.type == "article":
                print(f"{k.key} has no google scholar id")


def check_trailing_point_titles(entries):
    print("\nTitles with a trailing point:")
    for i in entries:
        title = i.fields.get("title")
        if title == None:
            if i.type != 'string':
                print(f"{i.key} has no title")
        else:
            title = strip_cb(title)
            if title[-1] == ".":
                print(f"{i.key}: {title}")


def check_doi(entries):
    print("\nJournal articles without a doi:")
    for i in entries:
        if i.type == 'article':
            doi = i.fields.get("doi")
            if doi == None:
                journal = i.fields.get("journal")
                year = i.fields.get("year")
                print(f"{i.key} in journal {journal} from year {year} has no doi")
    print("\nConference articles without a doi:")
    for i in entries:
        if i.type == 'inproceedings':
            doi = i.fields.get("doi")
            if doi == None:
                booktitle = i.fields.get("booktitle")
                year = i.fields.get("year")
                print(f"{i.key} in booktitle {booktitle} from year {year} has no doi")


def check_duplicates(entries):
    print("\nCheck possible duplicates:")
    for i in range(len(entries)):
        key1 = strip_cb(entries[i].key).lower()
        for j in range(i + 1, len(entries)):
            key2 = strip_cb(entries[j].key).lower()
            if key1 == key2:
                print("Possible duplicate entries " + entries[i].key + " and " + entries[j].key)


def check_keys(entries):
    print("\nCheck if keys have correct format:")
    for entry in entries:
        if entry.type in ["string"]:
            continue

        if len(entry.key) > 7:
            print(f"Key too long: {entry.key}")
            continue

        valid_key_format = re.match("^[A-Z][a-zA-Z]{1,3}[0-9]{2}[a-z]{0,1}$", entry.key)
        if not valid_key_format:
            print(f"Invalid key format: {entry.key}")
            continue

        if "year" not in entry.fields:
            print(f"{entry.key} does not have a year, so could not check if key matches year")
            continue

        entry_year = strip_cb(entry.fields["year"])
        readable_year = re.match("^[0-9]{4}$", entry_year)
        if not readable_year:
            print(f"{entry.key} does not have a readable year: {entry_year}")
            continue

        key_year = re.search("[0-9]{2}", entry.key).group()
        key_matches_year = key_year == entry_year[-2:]
        if not key_matches_year:
            print(f"Year in key {entry.key} ({key_year}) does not match field year {entry_year}")
            continue


def strip_curly_brackets(s):
    return s.replace("{", "").replace("}", "")


def make_month_dict():
    n_months = 12
    month_strings = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                     "November", "December"]
    month_dict = {}

    for m in range(n_months):
        standard = "{" + str(m + 1) + "}"

        possible_values = [
            m + 1,  # int(1)
            str(m + 1),  # 1
            f"{m + 1:02d}",  # 01
            month_strings[m],  # January
            month_strings[m].lower(),  # january
            month_strings[m][:3],  # Jan
            month_strings[m][:3].lower(),  # jan
            month_strings[m][:4],  # Janu
            month_strings[m][:4].lower(),  # janu
        ]

        for v in possible_values:
            month_dict[v] = standard  # Without { and }
            month_dict["{" + str(v) + "}"] = standard  # With { and }

    return month_dict


month_dict = make_month_dict()


def month_to_standard(month):
    if month not in month_dict:
        return None

    return month_dict[month]


def month_from_timestamp(timestamp):
    date = datetime.datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%SZ')

    return month_to_standard(date.month)


def month_from_crossref_item(item):
    dp = item["issued"]["date-parts"][0]
    if len(dp) >= 2:
        month = dp[1]
        return month_to_standard(month)
    else:
        return None


def month_from_doi(entry):
    if "doi" in entry.fields:
        doi = strip_curly_brackets(entry.fields["doi"])
    else:
        print(f"{entry.key}: No doi in entry, so could not find month")
        return None
    response = requests.get(f"https://api.crossref.org/works/{doi}")

    if not response.ok:
        print(f"{entry.key}: Could not find month from doi {doi}")
        return None

    res = response.json()
    item = res["message"]
    month = month_from_crossref_item(item)
    if month is None:
        print(f"{entry.key}: No issue date found in crossref api")
    else:
        return month


def month_from_arxiv_id(entry):
    if "journal" not in entry.fields:
        print(f"{entry.key}: While trying to find arxiv id: no journal")
        return None

    journal = strip_curly_brackets(entry.fields["journal"])
    if not journal.startswith("arXiv"):
        print(f"{entry.key}: While trying to find arxiv id: has journal tag, but is not an arxiv paper")
        return None

    arxiv_id = journal.split(":")[-1]
    timestamp = pyarxiv.query(ids=[arxiv_id])[0]["published"]

    return month_from_timestamp(timestamp)


def alpha_num_lower(s):
    return re.compile('[^a-zA-Z]').sub("", s).lower()


def month_from_title_and_author(entry):
    title = strip_curly_brackets(entry.fields["title"]).replace("&", "%26")
    authors = strip_curly_brackets(entry.fields["author"]).replace("&", "%26")
    url = f"https://api.crossref.org/works?rows=5&query.bibliographic={title} {authors}"
    response = requests.get(url)

    if not response.ok:
        print(f"{entry.key}: Could not load crossref api")
        return None

    res = response.json()
    items = res["message"]["items"]

    for item in items:
        if "author" not in item or "title" not in item:
            continue

        equal_title = alpha_num_lower(item["title"][0]) == alpha_num_lower(title)

        equal_authors = True

        for item_author in item["author"]:
            if "family" not in item_author or alpha_num_lower(item_author["family"]) not in alpha_num_lower(authors):
                equal_authors = False
                break

        if equal_title and equal_authors:
            month = month_from_crossref_item(item)
            if month is None:
                print(f"{entry.key}: No issue date found in crossref api")
            else:
                return month

    print(f"{entry.key}: No corresponding paper in crossref api found")

    return None


def check_months(entries):
    print("\nCheck if months are present and well formatted, and solve if possible:")
    entries_to_check = [e for e in entries if e.type != "string"]

    for entry in tqdm.tqdm(entries_to_check, desc="Checking months"):
        # Check if month key is already there
        if "month" in entry.fields:
            old_month = entry.fields["month"]
            month = month_to_standard(old_month)
            if month is None:
                print(f"{entry.key}: Unknown format for month {month}")
            else:
                entry.fields["month"] = month
                print(f"{entry.key}: formatted {old_month} to {month}")

            continue

        # Get month based on doi key
        doi_month = month_from_doi(entry)
        if doi_month is not None:
            entry.fields["month"] = doi_month
            print(f"{entry.key}: Found month {doi_month} for doi {entry.fields['doi']}")

            continue

        # Get month based on arXiv paper
        arxiv_month = month_from_arxiv_id(entry)
        if arxiv_month is not None:
            entry.fields["month"] = arxiv_month
            print(f"{entry.key}: Found month {arxiv_month} for arxiv {entry.fields['journal']}")

            continue

        # Get month based on title and authors
        ta_moth = month_from_title_and_author(entry)
        if ta_moth is not None:
            entry.fields["month"] = ta_moth
            print(f"{entry.key}: Found month {ta_moth} based on title and authors")

            continue

        print(f"{entry.key}: No month found at all")


def find_accent_string(main_string, accent_string):
    main_clean = unidecode(main_string).lower()
    accent_clean = ''.join([si for si in unidecode(accent_string).lower() if si.isalpha()])

    streak = ''
    found_start = -1
    found_end = -1
    for i, m in enumerate(main_clean):
        if not m.isalpha():
            continue

        if m == accent_clean[len(streak)]:
            if len(streak) == 0:
                found_start = i
            streak += m
        else:
            streak = ''
            continue

        if streak == accent_clean:
            found_end = i
            break

    return found_start, found_end


def check_accents(entries, field='author'):
    print("Checking accents from specific authors")
    authors_with_accents = [r"S\'{a}nchez", r"S\'{a}nchez-Guti\'{e}rrez"]

    for entry in entries:
        if field in entry.fields:
            for author in authors_with_accents:
                bib_authors = entry.fields[field]
                start, end = find_accent_string(bib_authors, author)
                found = bib_authors[start:end + 1]
                different = found != author
                if end != -1 and different:
                    print("Found one!")
                    print(bib_authors)
                    print(' ' * start + '^' + ' ' * (end - start - 1) + '$')

                    # Ask the user whether it should be replaced (this might not always be the case if someone with
                    # the same name except for the accent is meant).
                    answer = -1

                    while answer not in ['y', 'n']:
                        answer = input(f'Would you like to replace {found} with {author}? (y/n)')

                    if answer == 'y':
                        bib_authors = bib_authors[:start] + author + bib_authors[end + 1:]
                        entry.fields[field] = bib_authors
                        print('Changed author field to:')
                        print(bib_authors)
                    else:
                        print('Not changing author field')


def save_to_file(entries, fname, full_path=None):
    # sort on strings by key, followed by other entries
    keys = list()
    stringkeys = list()
    for i in entries:
        l = i.to_lines()
        if i.type == 'string':
            stringkeys.append(i.key)
        else:
            keys.append(i.key)
    keys.sort()
    stringkeys.sort()

    if full_path == None:
        file = io.open(literature_root + '/' + fname, 'w', newline='\r\n', encoding="utf-8")
    else:
        file = io.open(full_path, 'w', newline='\r\n', encoding="utf-8")

    for i in stringkeys:
        for j in entries:
            if j.key == i:
                file.writelines(j.to_lines())
                break
    for i in keys:
        for j in entries:
            if j.key == i:
                file.write("\n")
                file.writelines(j.to_lines())
                break

    file.close()


def creating_shared_link_password(dbx, path, password):
    link_settings = dropbox.sharing.SharedLinkSettings(
        requested_visibility=
        dropbox.sharing.RequestedVisibility.password,
        link_password=password,
        expires=datetime.datetime.utcnow() + datetime.timedelta(hours=24)
    )
    link = dbx.sharing_create_shared_link_with_settings(path, settings=link_settings)
    print(link.url)


def check_duplicates_among_bibfiles(diag_entries, diagnoweb_entries):
    print("\nChecking duplicates among bib files.")

    def to_check(entries):
        return [e for e in entries if e.type != "string"]

    diag_entries_to_check = to_check(diag_entries)
    diagnoweb_entries_to_check = to_check(diagnoweb_entries)

    def keys(entries):
        return [e.key for e in entries]

    diag_keys = keys(diag_entries_to_check)
    diagnoweb_keys = keys(diagnoweb_entries_to_check)

    duplicates = set(diag_keys).intersection(diagnoweb_keys)

    for key in duplicates:
        print(f"{key} is in both diagnoweb.bib and diag.bib.")


def check_encoding(input_file, output_file):
    # Compile regular expression that looks for two successive uncommon characters
    double_chars = re.compile(r"([^0-9a-zA-Z\s{}()\[\]<>.,;:?!_\-+=&/\\]{2,})")

    recoded = {}
    could_not_recode = []

    with Path(input_file).open("r", encoding="utf-8") as fp:
        for i, line in enumerate(fp):
            for match in double_chars.finditer(line):
                chars = match.group(0)

                try:
                    candidate = recode(chars)
                except ValueError:
                    could_not_recode.append(chars)
                else:
                    if len(candidate) == 1:
                        if chars not in recoded:
                            recoded[chars] = {
                                "candidate": candidate,
                                "examples": []
                            }

                        start, end = match.span()

                        recoded[chars]["examples"].append({
                            "line_number": i,
                            "span": (start, end),
                            "corrupt_line_value": line[start - 50:start] + "\033[1m" + colors.red(line[start:end]) +
                                                  "\033[0;0m" + line[end:end + 50]
                        })

    print(f"{len(recoded)} unique supsicious characters (or sequences of characters) were found")

    with Path(input_file).open("r", encoding="utf-8") as fp:
        full_text = fp.read()

    for orig_char, info in recoded.items():
        corrupt_line_examples = "\n".join([
            f'Line {ex["line_number"]}: {ex["corrupt_line_value"]}'
            for ex in info["examples"]])

        intro = "=" * 200 + \
                f"\nIn the following examples, the character \033[1m{colors.red(orig_char)}\033[0;0m might had " + \
                f"to be \033[1m{colors.yellow(info['candidate'])}\033[0;0m:\n\n" + \
                f"{corrupt_line_examples}.\n\n"

        print(intro)
        while True:
            answer = input("Is this correct (y/n/!newchar)?").lower()

            if answer == "y":
                full_text = full_text.replace(orig_char, info['candidate'])

                with Path(output_file).open("w", encoding="utf-8") as fp:
                    fp.write(full_text)

                break
            elif answer == "n":
                break
            elif answer.startswith("!"):
                # Replace the character with whatever comes after the !. This could also be empty.
                full_text = full_text.replace(orig_char, answer[1:])

                with Path(output_file).open("w", encoding="utf-8") as fp:
                    fp.write(full_text)
                break
            else:
                print(f"Answer '{answer}' not understood.")


if __name__ == '__main__':
    # =====================================
    # Checking bib files individually
    # =====================================
    entries = read_bibfile('diag.bib')

    gsdata = read_pop()
    add_gsid(gsdata, entries)
    update_gscites(gsdata, entries)

    # statistics(entries)
    # check_missing_pdfs(entries, False)
    # check_trailing_point_titles(entries)
    # check_doi(entries)
    # check_duplicates(entries)
    # check_keys(entries)
    # check_months(entries)
    # check_accents(entries, 'author')
    # check_accents(entries, 'copromotor')

    save_to_file(entries, 'diag1.bib')

    # =====================================
    # Comparing diag.bib and diagnoweb.bib
    # =====================================
    # diag_enries = read_bibfile('diag.bib')
    # diagnoweb_entries = read_bibfile('diagnoweb.bib')
    #
    # check_duplicates_among_bibfiles(diag_enries, diagnoweb_entries)

    # =====================================
    # Encoding
    # =====================================
    # check_encoding('diag.bib', 'diag.bib')
    # check_encoding('diagnoweb1.bib', 'diagnoweb1.bib')
