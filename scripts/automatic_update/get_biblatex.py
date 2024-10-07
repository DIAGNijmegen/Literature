import requests
from bs4 import BeautifulSoup
import re
import os
import sys
current_script_directory = os.path.dirname(os.path.realpath(__file__))
project_root = os.path.abspath(os.path.join(current_script_directory, os.pardir))
sys.path.append(os.path.join(project_root))
from script_data.accent_mappings import accent_mappings


class GetBiblatex:
    def __init__(self, doi, ss_id, diag_bib):
        self.doi = doi
        self.diag_bib = diag_bib
        self.ss_id = ss_id
        self.accent_mappings = accent_mappings

    def _get_doi_csl(self):
        """
        Main function to get doi information like authors, title, abstract
        :return: dictionary with doi information
        """
        response = requests.get(
            f"https://doi.org/{self.doi}",
            headers={"Accept": "application/vnd.citationstyles.csl+json"},
            timeout=20,
        )
        response.raise_for_status()

        return response.json()

    def _convert_to_biblatex_format(self, author_name):
        """
        :param author_name:
        :return:
        """
        for char, biblatex_char in self.accent_mappings.items():
            author_name = author_name.replace(char, biblatex_char)
        return author_name

    @staticmethod
    def _clean_abstract_text(abstract_string):
        # remove the <jats> </jats> from abstract text
        abstract_string = re.sub(pattern='<jats:\w+>', repl='', string=abstract_string)
        abstract_string = re.sub(pattern='</jats:\w+>', repl='', string=abstract_string)

        return abstract_string

    def _get_doi_abstract(self):
        header = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"}
        response_request = requests.get(f"https://doi.org/{self.doi}", headers=header,)
        soup = BeautifulSoup(response_request.content, "html.parser")

        abstract_text = False
        try:
            response = self._get_doi_csl()
            abstract_string = response['abstract']
            abstract_text = self._clean_abstract_text(abstract_string)
            complete = True
        except (KeyError, TypeError):
            complete = False

        if not abstract_text:
            abstract_text = 'Abstract unavailable'
            while not complete:
                methods = [{"name": "dc.description"}, {"name": "dc.Description"}, {"name": "twitter.description"}, {"property": "og:description"}]
                for method in methods:
                    try:
                        abstract_string = soup.find("meta", method)["content"]
                        if abstract_string[-3:] != '...':   # prevent using partially available abstracts
                            abstract_text = abstract_string
                            break
                    except TypeError:
                        pass
                complete = True

        # clean up abstract text with newlines
        if "\r\n" in abstract_text:
            abstract_text = abstract_text.replace("\r\n", " ")
        if "\n\n" in abstract_text:
            abstract_text = abstract_text.replace("\n\n", " ")

        return abstract_text

    @staticmethod
    def _clean_author_abbreviation(auth_abr, year, entries):
        """
        Creates a unique author-year key for a BibEntry based on existing entries.

        Args:
            auth_abr (str): The initial author abbreviation (e.g., 'Peet').
            year (str): Year of publication (e.g., '24' for 2024).
            entries (list): List of existing BibEntry objects with `entry.key` attributes.

        Returns:
            str: A unique author-year key (e.g., 'Peet24', 'Peet24a', 'Peet24b').
        """
        # Combine author abbreviation with the year to form the initial key (e.g., "Peet24").
        base_key = auth_abr + year

        # Collect existing keys to check for duplicates
        existing_keys = {entry.key for entry in entries}

        # If the base key is not in existing keys, return it as the new unique key
        if base_key not in existing_keys:
            return base_key

        # If the base key exists, try adding alphabetical suffixes (a, b, c, etc.) until a unique key is found
        letters = 'abcdefghijklmnopqrstuvwxyz'
        for letter in letters:
            new_key = base_key + letter  # Create new keys like "Peet24a", "Peet24b", etc.
            if new_key not in existing_keys:
                return new_key

        # If all single-letter suffixes are used, raise an error or use a different strategy
        raise ValueError(f"Could not generate a unique key for {base_key}. Consider expanding suffix options.")

    def get_bib_text(self):

        #try:
        response_json = self._get_doi_csl()
        abstract = self._get_doi_abstract()
        abstract = self._convert_to_biblatex_format(author_name=abstract)

        if 'proceedings-article' in response_json['type']:
            kind = 'inproceedings'
            journal = response_json['container-title']
        elif 'journal-article' in response_json['type']:
            kind = 'article'
            journal = response_json['container-title']
        elif 'article' in response_json['type']:
            kind = 'article'

            # Convert doi to arXiv journal format "arXiv:xxxx.xxxxx"
            index = self.doi.find('arXiv')
            arxiv_id = self.doi[index:]
            journal = arxiv_id.replace('.', ':', 1)
        elif 'book-chapter' in response_json['type']:
            kind = 'book'
            journal = response_json['container-title']

        elif 'posted-content' in response_json['type']:
            kind = 'article'
            journal = 'Preprint'

        author_string = "{"
        for index, author in enumerate(response_json["author"]):
            if index == len(response_json["author"])-1:
                if 'name' in author:  # Add research groups. For example COPD Investigators
                    author_string = author_string + f"{author['name']}" + "}"
                    continue
                if 'given' in author:
                    author_string = author_string + f"{author['family']}, {author['given']}" + "}"
                else:
                    author_string = author_string + f"{author['family']}" + "}"
            else:
                if 'name' in author:
                    author_string = author_string + f"{author['name']} and "
                    continue
                if 'given' in author:
                    author_string = author_string + f"{author['family']}, {author['given']} and "
                else:
                    author_string = author_string + f"{author['family']} and "
        author_string = self._convert_to_biblatex_format(author_name=author_string)
        newline = '\n'
        tab = '\t'
        author_abbreviation = response_json['author'][0]['family'].rsplit(' ')[-1]
        author_abbreviation = author_abbreviation.replace("'", "").lower().capitalize()[:4]

        published = response_json.get("published")
        if published is None:
            published = response_json.get("issued")
        year_short = str(published.get('date-parts')[0][0])[2:]
        year = str(published.get('date-parts')[0][0])
        # year = str(response_json["published"]["date-parts"][0][0])[2:]
        author_abbreviation = self._clean_author_abbreviation(author_abbreviation, year_short, self.diag_bib)
        title = response_json["title"]
        title = self._convert_to_biblatex_format(author_name=title)
        optnote = "DIAG, RADIOLOGY"
        self.ss_id = "['"+self.ss_id+"']"
        biblatex = f"@{kind}{{{author_abbreviation}, {newline}" \
                    f"{tab}author = {author_string}, {newline} " \
                    f"{tab}title = {{{title}}}, {newline}" \
                    f"{tab}doi = {{{response_json['DOI']}}}, {newline}" \
                    f"{tab}year = {{{year}}}, {newline}" \
                    f"{tab}abstract = {{{abstract}}}, {newline}" \
                    f"{tab}url = {{{response_json['URL']}}}, {newline}" \
                    f"{tab}file = {{{author_abbreviation}.pdf:pdf\\\\{author_abbreviation}.pdf:PDF}}, {newline}" \
                    f"{tab}optnote = {{{optnote}}}, {newline}" \
                    f"{tab}journal = {{{journal}}}, {newline}" \
                    f"{tab}automatic = {{yes}}, {newline}" \
                    f"{tab}all_ss_ids = {{{self.ss_id}}}, {newline}"
                    # f"{tab}citation-count = {{{response_json['is-referenced-by-count']}}}, {newline}" \
                    # f"}}{newline}"
        
        if 'is-referenced-by-count' in response_json.keys():
            biblatex = biblatex + f"{tab}citation-count = {{{response_json['is-referenced-by-count']}}}, {newline}"

        if 'page' in response_json.keys() and 'volume' in response_json.keys():
            biblatex = biblatex + f"{tab}pages = {{{response_json['page']}}},{newline}" + f"{tab}volume = {{{response_json['volume']}}}, {newline}" + f"}}{newline}"
        elif 'page' in response_json.keys():
            biblatex = biblatex + f"{tab}pages = {{{response_json['page']}}},{newline}" + f"}}{newline}"
        elif 'volume' in response_json.keys():
            biblatex = biblatex + f"{tab}volume = {{{response_json['volume']}}}, {newline}" + f"}}{newline}"
        else:
            biblatex = biblatex + f"}}{newline}"

        # replace any EN DASH with Hyphen
        if "–" in biblatex:
            biblatex = biblatex.replace("–", "-")

        #except Exception as e:
        #    print(f'Unable to generate bibtext for {self.doi}')
        #    print(e)
        #    biblatex = 'empty'

        return biblatex
