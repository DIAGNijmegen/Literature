import sys
import time
import logging
from pathlib import Path
from datetime import datetime
import pandas as pd
import requests
from difflib import SequenceMatcher
import string
import yaml

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DATE = datetime.now().strftime("%Y%m%d")
AUTOMATIC_UPDATE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = AUTOMATIC_UPDATE_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parent

sys.path.append(str(SCRIPTS_DIR))
from bib_handling_code.processbib import read_bibfile

ACTIONS = '[add ss_id, blacklist ss_id, add new item, add manually, update_item, None]'

# (File) folders relative to repository root
CONFIG = {
    "bib_path": REPO_ROOT / "diag.bib",
    "output_dir": SCRIPTS_DIR / "script_data",
    "blacklist_path": SCRIPTS_DIR / "script_data" / "blacklist.csv",
    "manual_check_path": SCRIPTS_DIR / "script_data" / f"manual_check_{DATE}.xlsx",
    "retrieved_items_blacklisted_path": SCRIPTS_DIR / "script_data" / f"retrieved_blacklisted_items_{DATE}.xlsx",
    "min_year": 2015,
}

CONFIG_FILE = AUTOMATIC_UPDATE_DIR / "config.yaml"
with open(CONFIG_FILE, "r") as file:
    config_data = yaml.safe_load(file)

STAFF_IDS = config_data["STAFF_IDS"]
STAFF_YEARS = config_data["STAFF_YEARS"]


def normalize_doi(doi):
    # Convert to lowercase
    doi = doi.lower()
    # Remove 'https://doi.org/' if present
    if doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]
    return doi


def return_existing_dois(df_bib):
    all_dois=[]
    for idx, row in df_bib.iterrows():
        if row['doi'] != '':
            all_dois.append(normalize_doi(row['doi']))
    return all_dois


def save_excel(df, file_name, sort_by=None):
    """Save DataFrame to an Excel file."""
    if sort_by:
        df = df.sort_values(by=sort_by)
    df.to_excel(file_name, index=False)
    logging.info(f"Saved DataFrame to {file_name}")


def fetch_with_retry(url, max_retries=5):
    wait = 1  # start with 1 second
    for attempt in range(max_retries):
        r = requests.get(url)
        if r.status_code == 200:
            return r
        elif r.status_code == 429:
            print(f"Rate limit hit. Waiting {wait}s...")
            time.sleep(wait)
            wait *= 2  # exponential backoff
        else:
            r.raise_for_status()
    raise Exception(f"Failed after {max_retries} retries for {url}")


def remove_blacklist_items(df_new_items, blacklist_path):
    """Remove blacklisted items from the final DataFrame and save removed items to a CSV."""
    blacklisted_items = pd.read_csv(blacklist_path)

    mask_ss_id = df_new_items['ss_id'].isin(blacklisted_items['ss_id'].unique())
    mask_ss_doi = df_new_items['ss_doi'].isin(blacklisted_items['doi'].unique()) & df_new_items['ss_doi'].notna()

    combined_mask = mask_ss_id | mask_ss_doi
    removed_items = df_new_items[combined_mask].copy()
    df_new_items = df_new_items[~combined_mask].copy()

    logging.info(f"{len(removed_items)} items removed from newly found items.")
    return df_new_items, removed_items


def from_bib_to_df(diag_bib_raw):
    """Convert bib file to a df."""
    bib_fields = ['title', 'authors', 'doi', 'gscites', 'journal', 'year', 'all_ss_ids', 'pmid']

    bib_data = [
        [entry.key, entry.type, *(entry.fields.get(f, '').strip('{}') for f in bib_fields)]
        for entry in diag_bib_raw
        if entry.type != 'string'
    ]

    columns = ['bibkey', 'type', *bib_fields]
    return pd.DataFrame(bib_data, columns=columns)


def find_new_ssids():
    """ Find new items from Semantic Scholar, based on staff IDs and years. Returns a DataFrame with all paper info for staff members. """
    all_staff_id_ss_data = []

    for staff_name, staff_ids in STAFF_IDS.items():
        print('Processing staff member:', staff_name)

        staff_years = STAFF_YEARS.get(staff_name)
        if not staff_years:
            logging.warning(f"No year information found for staff member: {staff_name}. Skipping.")
            continue

        staff_start = staff_years['start']
        staff_end = staff_years['end']

        for staff_id in staff_ids:
            print('\t\t', staff_id)

            url = (f'https://api.semanticscholar.org/graph/v1/author/{staff_id}/papers?fields=year,title,authors,externalIds,citationCount,publicationTypes,journal&limit=500')
            r = fetch_with_retry(url)

            ss_staff_data = r.json().get('data', [])

            for entry in ss_staff_data:
                ss_year = entry.get('year')
                if ss_year is not None:
                    ss_year = int(ss_year)
                    if ss_year < CONFIG['min_year']:
                        continue
                    if not staff_start <= ss_year <= staff_end: 
                        continue

                authors = ' and '.join([author['name'] for author in entry.get('authors', [])])
                journal = entry.get('journal', None)
                journal_name = journal.get('name') if journal else None

                all_staff_id_ss_data.append([
                    staff_id, staff_name, staff_start, staff_end, ss_year, 
                    entry.get('paperId'), 
                    entry.get('title'), 
                    entry['externalIds'].get('DOI'), 
                    entry.get('citationCount'), 
                    entry['externalIds'].get('PubMed'), 
                    authors, 
                    journal_name
                ])
                
    columns = ['staff_id', 'staff_name', 'staff_from', 'staff_till', 'ss_year', 'ss_id', 'title', 'doi', 'ss_citations', 'pmid', 'authors', 'journal']
    df_all_staff_id_ss_data = pd.DataFrame(all_staff_id_ss_data, columns=columns)
   # df_all_staff_id_ss_data = df_all_staff_id_ss_data.drop_duplicates(subset=['ss_id'])

    df_all_staff_id_ss_data = (
        df_all_staff_id_ss_data
        .sort_values(by=['ss_id', 'staff_id'])
        .drop_duplicates(subset=['ss_id'], keep='first')
        .reset_index(drop=True)
    )

    print('DONE')
    return df_all_staff_id_ss_data


def find_doi_match(df_bib, df_found_items, actions_list):
    """Find DOI matches between the bib items and found items."""
    
    list_doi_match = []     # Bib entry & SS paper matched by DOI
    not_new = []            # SS papers already known: ignore
    ss_id_match = []        # SS papers matched via DOI but not linked yet
    update_item = []        # Bib entry should be updated (e.g., arXiv to DOI)
    update_item_ssid = []   # ss_ids that will be updated.

    found_items = df_found_items['ss_id'].tolist()

    found_by_ssid = df_found_items.set_index('ss_id')
    found_items_set = set(found_by_ssid.index)

    found_doi_to_ssid = {
        doi: ss_id
        for ss_id, doi in zip(df_found_items['ss_id'], df_found_items['doi'])
        if doi
    }

    all_dois = return_existing_dois(df_bib)

    for _, row in df_bib.iterrows():
        bib_iloc0 = row.iloc[0]
        bib_iloc1 = row.iloc[1]
        bib_iloc2 = row.iloc[2]
        bib_iloc3 = row.iloc[3]
        bib_doi_raw = row.iloc[4]
        bib_iloc6= row.iloc[6]
        bib_iloc7 = row.iloc[7]
        bib_doi = normalize_doi(bib_doi_raw) if bib_doi_raw else ''

        ss_ids_raw = row.iloc[8]

        if ss_ids_raw:
            all_ss_ids = [
                el.translate(str.maketrans('', '', string.punctuation)).strip()
                for el in ss_ids_raw.split(',')
                if el.strip()
            ]
        else:
            all_ss_ids = []

        # Check if any existing bib-item has the same ss_id as an item on found_items_set 
        for ss_id in all_ss_ids:
            if not ss_id in found_items_set:
                continue 
            
            found = found_by_ssid.loc[ss_id]
            ss_doi_raw = found.doi

            if not ss_doi_raw:
                not_new.append(ss_id)
                continue

            ss_doi = normalize_doi(ss_doi_raw)
            if ss_doi != bib_doi and ss_doi not in all_dois and (bib_doi == '' or 'arxiv' in bib_doi):
                found = found_by_ssid.loc[ss_id]

                update_item.append((bib_iloc0, ss_id, f'https://www.semanticscholar.org/paper/{ss_id}',
                                     1, bib_doi, ss_doi, bib_iloc2, found.title, found.staff_id, found.staff_name, bib_iloc3, 
                                    found.authors, bib_iloc6, 
                                    found.journal, 
                                    bib_iloc7, found.ss_year, bib_iloc1, 
                                    found.pmid, 'update item', actions_list))
                update_item_ssid.append(ss_id)
            else:
                not_new.append(ss_id)

        # Check if any existing bib-item has the same doi as an item on found_items
        if bib_doi and bib_doi in found_doi_to_ssid:
            ss_id = found_doi_to_ssid[bib_doi]

            # Check if that bib-item is already linked with the ss_id
            if ss_id not in all_ss_ids:
                found = found_by_ssid.loc[ss_id]

                ss_title=found.title
                ratio = SequenceMatcher(a=ss_title,b=bib_iloc2).ratio()

                ss_id_match.append(ss_id)
                list_doi_match.append((bib_iloc0, ss_id, 'https://www.semanticscholar.org/paper/'+ss_id, ratio, bib_doi, bib_doi, bib_iloc2, ss_title, int(found.staff_id), 
                                       found.staff_name, bib_iloc3, found.authors, bib_iloc6, found.journal, bib_iloc7, int(found.ss_year), bib_iloc1, found.pmid, 'doi match', 
                                       actions_list))
    
    to_add = set(found_items)-set(not_new)-set(ss_id_match)-set(update_item_ssid)
    
    # Remove ss_ids that are already in bibfile and ss_id with doi match
    new_items = df_found_items[df_found_items['ss_id'].isin(to_add)]
    return new_items, list_doi_match, update_item

def classify_found_item(df_bib, found_item):
    """
    Classify a single found item against the bib file.

    Args:
        df_bib (DataFrame)
        found_item (dict-like or Series):
            must contain at least: ss_id, doi, title

    Returns:
        category (str): one of {'not_new', 'update_item', 'doi_match', 'new_item'}
        info (dict | None): minimal info needed by the caller
    """

    ss_id = found_item['ss_id']
    ss_doi = normalize_doi(found_item['doi']) if found_item['doi'] else ''
    ss_title = found_item['title']

    all_dois = return_existing_dois(df_bib)

    for _, row in df_bib.iterrows():
        bib_doi = normalize_doi(row.iloc[4]) if row.iloc[4] else ''
        ss_ids = row.iloc[8]

        all_ss_ids = []
        if ss_ids:
            all_ss_ids = [
                el.translate(str.maketrans('', '', string.punctuation)).strip()
                for el in ss_ids.split(',')
            ]

        # 1. SS ID already known
        if ss_id in all_ss_ids:
            if ss_doi and ss_doi not in all_dois and (bib_doi == '' or 'arxiv' in bib_doi):
                return (
                    'update_item',
                    {
                        'bib_id': row.iloc[0],
                        'ss_id': ss_id,
                        'old_doi': bib_doi,
                        'new_doi': ss_doi
                    }
                )
            else:
                return ('not_new', {'ss_id': ss_id})

        # 2. DOI match, SS ID not linked yet
        if bib_doi and ss_doi and bib_doi == ss_doi:
            ratio = SequenceMatcher(a=ss_title, b=row.iloc[2]).ratio()
            return (
                'doi_match',
                {
                    'bib_id': row.iloc[0],
                    'ss_id': ss_id,
                    'similarity': ratio
                }
            )
    # 3. Brand-new item
    return ('new_item', None)



def find_title_match_or_new_items(new_items, df_bib, actions_list):
    """Find title matches or new items between the bib file and found items."""
    
    titles = new_items['title'].tolist()
    dois = new_items['doi'].tolist()
    ss_ids = new_items['ss_id'].tolist()
    list_title_match = []
    list_no_dois = []
    list_to_add = []
    
    for ss_id, ss_title, doi in zip(ss_ids, titles, dois):
        title_match_ratios = df_bib['title'].apply(lambda x: SequenceMatcher(
                a=ss_title.lower(), 
                b=x.lower().replace('{', '').replace('}', '')).ratio())
        max_ratio = title_match_ratios.max()
        max_bibkey = df_bib[title_match_ratios==max_ratio]['bibkey'].iloc[0]
        max_bib_title = df_bib[title_match_ratios==max_ratio]['title'].iloc[0]
        max_bib_title = max_bib_title.replace('{', '').replace('}', '')
        if sum(title_match_ratios>0.8) >= 1:
            up80_bib_entries = df_bib[title_match_ratios > 0.8]
            for i, match in up80_bib_entries.iterrows():
                list_title_match.append((
                    match['bibkey'],
                    ss_id,
                    f'https://www.semanticscholar.org/paper/{ss_id}',
                    title_match_ratios[i],
                    match['doi'],
                    doi,
                    match['title'].replace('{', '').replace('}', ''),
                    ss_title,
                    new_items[new_items['ss_id'] == ss_id]['staff_id'].item(),
                    new_items[new_items['ss_id'] == ss_id]['staff_name'].item(),
                    match['authors'],
                    new_items[new_items['ss_id'] == ss_id]['authors'].item(),
                    match['journal'],
                    new_items[new_items['ss_id'] == ss_id]['journal'].item(),
                    match['year'],
                    new_items[new_items['ss_id'] == ss_id]['ss_year'].item(),
                    match['type'],
                    new_items[new_items['ss_id'] == ss_id]['pmid'].item(),
                    'title match', actions_list))
        else:
            max_bib_entry = df_bib[title_match_ratios == max_ratio]
            authors = max_bib_entry['authors'].iloc[0]
            bib_doi = max_bib_entry['doi'].iloc[0]
            max_bib_journal = max_bib_entry['journal'].iloc[0]
            max_bib_year = max_bib_entry['year'].iloc[0]
            type_article = max_bib_entry['type'].iloc[0]
            
            ss_authors = new_items[new_items['ss_id'] == ss_id]['authors'].item()
            staff_id = new_items[new_items['ss_id'] == ss_id]['staff_id'].item()
            staff_name = new_items[new_items['ss_id'] == ss_id]['staff_name'].item()
            ss_journal_name = new_items[new_items['ss_id'] == ss_id]['journal'].item()
            ss_year = new_items[new_items['ss_id'] == ss_id]['ss_year'].item()
            ss_pmid = new_items[new_items['ss_id'] == ss_id]['pmid'].item()
            
            if doi is None:
                list_no_dois.append((max_bibkey, ss_id, f'https://www.semanticscholar.org/paper/{ss_id}', max_ratio, bib_doi, doi, max_bib_title, ss_title, staff_id, staff_name, authors, ss_authors, max_bib_journal, ss_journal_name, max_bib_year, ss_year, type_article, ss_pmid, 'doi None', actions_list))
            else:
                list_to_add.append((max_bibkey, ss_id, f'https://www.semanticscholar.org/paper/{ss_id}', max_ratio, bib_doi, doi, max_bib_title, ss_title, staff_id, staff_name, authors, ss_authors, max_bib_journal, ss_journal_name, max_bib_year, ss_year, type_article, ss_pmid, 'new item', actions_list))
    return list_title_match, list_no_dois, list_to_add


def main():
    #TODO: check out the double removal of blacklisted items
    diag_bib_raw = read_bibfile(None, CONFIG['bib_path'])
    df_bib = from_bib_to_df(diag_bib_raw)

    df_found_items = find_new_ssids()
    new_items, list_doi_match, update_item = find_doi_match(df_bib, df_found_items, ACTIONS)
    
    blacklist = pd.read_csv(CONFIG['blacklist_path'])
    normalized_blacklist_dois = set(normalize_doi(str(doi)) for doi in blacklist['doi'].dropna().unique())
    new_items = new_items[~new_items['doi'].apply(lambda x: normalize_doi(str(x)) if pd.notna(x) else '').isin(normalized_blacklist_dois)]
    
    # Find title matches, items without dois, new items
    list_title_match, list_no_dois, list_to_add = find_title_match_or_new_items(new_items, df_bib, ACTIONS)
    
    total_list = list_to_add + list_no_dois + list_title_match + list_doi_match + update_item

    columns = ['bibkey', 'ss_id', 'url', 'match score', 'bib_doi', 'ss_doi', 'bib_title', 'ss_title', 'staff_id', 'staff_name', 'bib_authors', 'ss_authors', 'bib_journal', 'ss_journal', 'bib_year', 'ss_year', 'bib_type', 'ss_pmid', 'reason', 'action']
    df=pd.DataFrame(total_list, columns=columns)

    # TODO: Save .csv instead of .xlsx
    df_new_items, removed_items = remove_blacklist_items(df, CONFIG['blacklist_path'])
    save_excel(removed_items, CONFIG['retrieved_items_blacklisted_path'])
    save_excel(df_new_items, CONFIG['manual_check_path'], sort_by=['ss_id'])


if __name__ == "__main__":
    main()