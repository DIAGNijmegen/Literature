import sys
import time
import logging
from pathlib import Path
from datetime import datetime
import pandas as pd
import requests
from difflib import SequenceMatcher
import ast
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

COLUMNS_EXCEL = ['bibkey', 'ss_id', 'url', 'match score', 'bib_doi', 'ss_doi', 'bib_title', 'ss_title', 
                 'staff_id', 'staff_name', 'bib_authors', 'ss_authors', 'bib_journal', 'ss_journal', 
                 'bib_year', 'ss_year', 'bib_type', 'ss_pmid', 'reason', 'action']

def normalize_doi(doi):
    if not doi:
        return ''
    doi = str(doi).lower().strip()
    if doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]
    return doi


def fetch_with_retry(url, max_retries=5):
    wait = 1

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url)
            if r.status_code == 429:
                time.sleep(wait)
                wait *= 2
                continue
            r.raise_for_status()
            return r
        
        except (requests.RequestException, ValueError) as e:
            logging.warning(f"[{attempt}/{max_retries}] Request failed: {e}")
            if attempt == max_retries:
                raise
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


#BIB_FIELDS = {'title', 'author', 'doi', 'gscites','journal', 'year', 'all_ss_ids', 'pmid'}

#def from_bib_to_df(diag_bib_raw):
#    rows = []

#    for entry in diag_bib_raw:
#        if entry.type == 'string':
#            continue

#        row = {
#            'bibkey': entry.key,
#            'type': entry.type,
#        }

#        for k, v in entry.fields.items():
#            if k in BIB_FIELDS:
#                row[k] = v

#        rows.append(row)

#    return pd.DataFrame(rows)


def entry_withing_valid_time(ss_year, staff_start, staff_end):
    if ss_year is not None:
        ss_year = int(ss_year)
        if ss_year < CONFIG['min_year']:
            return False
        if not staff_start <= ss_year <= staff_end: 
            return False
    return True

def preprocess_bib(df_bib):
    all_dois = set()
    doi_to_row = {}
    ssid_to_row = {}

    for _, row in df_bib.iterrows():
        bib_doi_raw = row['doi']
        bib_doi = normalize_doi(bib_doi_raw) if bib_doi_raw else ''
        if bib_doi:
            all_dois.add(bib_doi)
            doi_to_row[bib_doi] = row

        ss_ids = row['all_ss_ids'] or []
        if isinstance(ss_ids, str):
            try:
                ss_ids = ast.literal_eval(ss_ids)
            except (ValueError, SyntaxError):
                ss_ids = []
        
        if isinstance(ss_ids, str):
            ss_ids = ss_ids.replace(',', ' ').split()

        for ssid in ss_ids:
            ssid = ssid.strip()
            if ssid:
                ssid_to_row[ssid] = row

    return all_dois, doi_to_row, ssid_to_row


def find_new_ssids(df_bib):
    """ Find new items from Semantic Scholar, based on staff IDs and years. Returns a DataFrame with all paper info for staff members. """
    new_items = []
    doi_matches = []
    update_items = []
    not_new = []

    all_dois, doi_to_row, ssid_to_row = preprocess_bib(df_bib)

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
                if not entry_withing_valid_time(ss_year, staff_start, staff_end):
                    continue

                found_item = get_found_item_dict(entry, staff_id, staff_name, staff_start, staff_end, ss_year)     
                category, info = find_doi_match(all_dois, doi_to_row, ssid_to_row, found_item)
                
                if category == 'new_item':
                    new_items.append(found_item)
                elif category == 'update_item':
                    update_items.append(info)
                elif category == 'doi_match':
                    doi_matches.append(info)
                elif category == 'not_new':
                    not_new.append(info)

    new_items_df = pd.DataFrame(new_items).drop_duplicates(subset=['ss_id'])
    doi_matches_df = pd.DataFrame(doi_matches).drop_duplicates(subset=['ss_id'])
    update_items_df = pd.DataFrame(update_items).drop_duplicates(subset=['ss_id'])

    return new_items_df, doi_matches_df, update_items_df

def get_found_item_dict(entry, staff_id, staff_name, staff_start, staff_end, ss_year):
    authors = ' and '.join([author['name'] for author in entry.get('authors', [])])
    journal = entry.get('journal', None)
    journal_name = journal.get('name') if journal else None

    found_item = {
        'staff_id': staff_id,
        'staff_name': staff_name,
        'staff_from': staff_start,
        'staff_till': staff_end,
        'ss_year': ss_year,
        'ss_id': entry.get('paperId'),
        'ss_title': entry.get('title'),
        'ss_doi': entry['externalIds'].get('DOI'),
        'ss_citations': entry.get('citationCount'),
        'ss_pmid': entry['externalIds'].get('PubMed'),
        'ss_authors': authors,
        'ss_journal': journal_name,
    }           
    return found_item
                
def _build_row(row, found_item, ratio, reason):
    return (
        found_item
        | {
            'bibkey': row.iloc[0], 
            'url': f'https://www.semanticscholar.org/paper/{found_item["ss_id"]}',
            'match_score': ratio, 
            'bib_doi': row.iloc[4], 
            'ss_doi': row.iloc[4], 
            'bib_title': row.iloc[2], 
            'bib_authors': row.iloc[3],
            'bib_journal': row.iloc[6], 
            'bib_year': row.iloc[7], 
            'bib_type': row.iloc[1], 
            'reason': reason, 
            'actionn': ACTIONS
        }
    )

def find_doi_match(all_dois, doi_to_row, ssid_to_row, found_item):
    """Find DOI matches between the bib items and found items."""
    
    ss_id = found_item['ss_id']
    ss_doi = normalize_doi(found_item['ss_doi'])

    # Match by staff ID
    if ss_id in ssid_to_row:
        row = ssid_to_row[ss_id]
        bib_doi_raw = row.iloc[4]
        bib_doi = normalize_doi(bib_doi_raw) if bib_doi_raw else ''

        if ss_doi != bib_doi and ss_doi not in all_dois and (bib_doi == '' or 'arxiv' in bib_doi):
            return 'update_item', _build_row(row, found_item, 1, 'update_item')
        else:
            return 'not_new', ss_id
    
    # Match by DOI
    if ss_doi and ss_doi in doi_to_row:
        row = doi_to_row[ss_doi]
        bib_iloc2 = row.iloc[2]
        ss_title = found_item['ss_title']
        ratio = SequenceMatcher(a=ss_title, b=bib_iloc2).ratio()
        return 'doi_match', _build_row(row, found_item, ratio, 'doi_match')
            
    return 'new_item', None


def find_title_match_or_new_items(new_items, df_bib):
    """Find title matches or new items between the bib file and found items."""
    
    titles = new_items['ss_title'].tolist()
    dois = new_items['ss_doi'].tolist()
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
                    new_items[new_items['ss_id'] == ss_id]['ss_authors'].item(),
                    match['journal'],
                    new_items[new_items['ss_id'] == ss_id]['ss_journal'].item(),
                    match['year'],
                    new_items[new_items['ss_id'] == ss_id]['ss_year'].item(),
                    match['type'],
                    new_items[new_items['ss_id'] == ss_id]['ss_pmid'].item(),
                    'title match', ACTIONS))
        else:
            max_bib_entry = df_bib[title_match_ratios == max_ratio]
            authors = max_bib_entry['authors'].iloc[0]
            bib_doi = max_bib_entry['doi'].iloc[0]
            max_bib_journal = max_bib_entry['journal'].iloc[0]
            max_bib_year = max_bib_entry['year'].iloc[0]
            type_article = max_bib_entry['type'].iloc[0]
            
            ss_authors = new_items[new_items['ss_id'] == ss_id]['ss_authors'].item()
            staff_id = new_items[new_items['ss_id'] == ss_id]['staff_id'].item()
            staff_name = new_items[new_items['ss_id'] == ss_id]['staff_name'].item()
            ss_journal_name = new_items[new_items['ss_id'] == ss_id]['ss_journal'].item()
            ss_year = new_items[new_items['ss_id'] == ss_id]['ss_year'].item()
            ss_pmid = new_items[new_items['ss_id'] == ss_id]['ss_pmid'].item()
            
            if doi is None:
                list_no_dois.append((max_bibkey, ss_id, f'https://www.semanticscholar.org/paper/{ss_id}', max_ratio, bib_doi, doi, max_bib_title, ss_title, staff_id, 
                                     staff_name, authors, ss_authors, max_bib_journal, ss_journal_name, max_bib_year, ss_year, type_article, ss_pmid, 'doi None', ACTIONS))
            else:
                list_to_add.append((max_bibkey, ss_id, f'https://www.semanticscholar.org/paper/{ss_id}', max_ratio, bib_doi, doi, max_bib_title, ss_title, staff_id, 
                                    staff_name, authors, ss_authors, max_bib_journal, ss_journal_name, max_bib_year, ss_year, type_article, ss_pmid, 'new item', ACTIONS))
    return list_title_match, list_no_dois, list_to_add


def normalize_records(records, columns=COLUMNS_EXCEL):
    """
    Convert a list of dicts or tuples into a list of dicts
    with the same columns. Missing values are filled with NA.
    """
    if records is None:
        return []

    if isinstance(records, pd.DataFrame):
        records = records.to_dict(orient="records")

    normalized = []

    for r in records:
        if isinstance(r, dict):
            normalized.append({c: r.get(c, pd.NA) for c in columns})

        elif isinstance(r, (list, tuple)):
            # assume positional order matches columns
            normalized.append({
                c: r[i] if i < len(r) else pd.NA
                for i, c in enumerate(columns)
            })

        else:
            raise TypeError(f"Unsupported record type: {type(r)}")

    return normalized


def main():
    diag_bib_raw = read_bibfile(None, CONFIG['bib_path'])
    logging.info(f"Bib file read: {len(diag_bib_raw)} entries")

    df_bib = from_bib_to_df(diag_bib_raw)
    logging.info(f"Converted to DataFrame: {len(df_bib)} rows")

    new_items, list_doi_match, update_item = find_new_ssids(df_bib)
    logging.info(f"New items: {len(new_items)}, DOI matches: {len(list_doi_match)}, Updates: {len(update_item)}")

    blacklist = pd.read_csv(CONFIG['blacklist_path'])
    normalized_blacklist_dois = set(normalize_doi(str(doi)) for doi in blacklist['doi'].dropna().unique())
    new_items_before_blacklist = len(new_items)
    new_items = new_items[~new_items['ss_doi'].apply(lambda x: normalize_doi(str(x)) if pd.notna(x) else '').isin(normalized_blacklist_dois)]
    logging.info(f"Removed {new_items_before_blacklist - len(new_items)} blacklisted DOIs")

    list_title_match, list_no_dois, list_to_add = find_title_match_or_new_items(new_items, df_bib)
    
    logging.info(f"Title matches: {len(list_title_match)}, No DOI: {len(list_no_dois)}, To add: {len(list_to_add)}, DOI match: {len(list_doi_match)}, update items {len(update_item)}")
    total_list = (
        normalize_records(list_to_add)
        + normalize_records(list_no_dois)
        + normalize_records(list_title_match)
        + normalize_records(list_doi_match)
        + normalize_records(update_item)
    )
    logging.info(f"Total items to write: {len(total_list)}")

    df=pd.DataFrame(total_list, columns=COLUMNS_EXCEL)

    # TODO: Save .csv instead of .xlsx
    df_new_items, removed_items = remove_blacklist_items(df, CONFIG['blacklist_path'])
    save_excel(removed_items, CONFIG['retrieved_items_blacklisted_path'])
    save_excel(df_new_items, CONFIG['manual_check_path'], sort_by=['ss_id'])


def save_excel(df, file_name, sort_by=None):
    """Save DataFrame to an Excel file."""
    if sort_by:
        df = df.sort_values(by=sort_by)
    df.to_excel(file_name, index=False)
    logging.info(f"Saved DataFrame to {file_name}")

if __name__ == "__main__":
    main()