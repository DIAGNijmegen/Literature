import sys
import logging
from pathlib import Path
from datetime import datetime
import pandas as pd
import requests
from difflib import SequenceMatcher
import string

DATE = datetime.now().strftime("%Y%m%d")

AUTOMATIC_UPDATE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = AUTOMATIC_UPDATE_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parent

sys.path.append(str(SCRIPTS_DIR))
from bib_handling_code.processbib import read_bibfile

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# (File) folders relative to repository root
CONFIG = {
    "bib_path": REPO_ROOT / "diag.bib",
    "output_dir": SCRIPTS_DIR / "script_data",
    "blacklist_path": SCRIPTS_DIR / "script_data" / "blacklist.csv",
    "manual_check_path": SCRIPTS_DIR / "script_data" / f"manual_check_{DATE}.xlsx",
    "retrieved_items_blacklisted_path": SCRIPTS_DIR / "script_data" / f"retrieved_blacklisted_items_{DATE}.xlsx",
    "min_year": 2015,
}

staff_id_dict = {'Bram van Ginneken': [8038506, 123637526, 2238811617, 2237665783, 2064076416],
'Francesco Ciompi': [143613202, 2246376566, 2291304571, 2370540204],
'Alessa Hering': [153744566],
'Henkjan Huisman': [34754023, 2247422768, 2242473717, 2275450757],
'Colin Jacobs': [2895994, 2281807058, 2331492473, 2262970009, 2368325830],
'Peter Koopmans': [34726383],
'Jeroen van der Laak': [145441238, 145388932, 2347447, 2255290517, 2259038560],
'Geert Litjens': [145959882],
'James Meakin': [4960344],
'Keelin Murphy': [35730362],
'Ajay Patel': [2109170880, 2116215861],
'Cornelia Schaefer-Prokop': [1419819133, 1445069528, 1400632685, 2242581221, 2262278647, 2240604857, 2250313247],
'Matthieu Rutten': [2074975080, 2156546, 47920520, 2238355627, 2239745868],
'Jos Thannhauser': [5752941],
"Bram Platel" : [1798137], 
"Nico Karssemeijer" : [1745574], 
"Clarisa Sanchez" : [144085811, 32187701], 
"Nikolas Lessman" : [2913408], 
"Jonas Teuwen" : [32649341, 119024451, 2259899370], 
"Rashindra Manniesing" : [2657081],
"Nadieh Khalili": [144870959]}

staff_year_dict = {
'Bram van Ginneken':  {'start' : 1996, 'end': 9999},
'Francesco Ciompi':  {'start' : 2013, 'end': 9999},
'Alessa Hering':  {'start' : 2018, 'end': 9999},
'Henkjan Huisman':  {'start' : 1992, 'end': 9999},
'Colin Jacobs':  {'start' : 2010, 'end': 9999},
'Peter Koopmans':  {'start' : 2022, 'end': 9999},
'Jeroen van der Laak':  {'start' : 1991, 'end': 9999},
'Geert Litjens':  {'start' : 2016, 'end': 9999},
'James Meakin':  {'start' : 2017, 'end': 9999},
'Keelin Murphy':  {'start' : 2018, 'end': 9999},
'Ajay Patel':  {'start' : 2015, 'end': 9999},
'Cornelia Schaefer-Prokop':  {'start' : 2010, 'end': 9999},
'Matthieu Rutten':  {'start' : 2019, 'end': 9999},
'Jos Thannhauser': {'start' : 2022, 'end': 9999},
"Bram Platel" : {'start' : 2010,  'end' : 2019},
"Nico Karssemeijer" : {'start' : 1989, 'end' : 2022}, 
"Clarisa Sanchez" : {'start' : 2008, 'end' : 2021}, 
"Nikolas Lessman" : {'start' : 2019, 'end' : 2022}, 
"Jonas Teuwen" : {'start' : 2017, 'end' : 2020}, 
"Rashindra Manniesing" : {'start' : 2010, 'end' : 2021},
"Nadieh Khalili" : {'start' : 2023, 'end' : 9999}
}

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


def remove_blacklist_items(df_new_items, blacklist_path):
    """Remove blacklisted items from the final DataFrame and save removed items to a CSV."""
    blacklisted_items = pd.read_csv(blacklist_path)

    mask_ss_id = df_new_items['ss_id'].isin(blacklisted_items['ss_id'].unique())
    mask_ss_doi = df_new_items['ss_doi'].isin(blacklisted_items['doi'].unique()) & df_new_items['ss_doi'].notna()

    combined_mask = mask_ss_id | mask_ss_doi
    removed_items = df_new_items[combined_mask]
    df_new_items = df_new_items[~combined_mask]

    logging.info(f"{len(removed_items)} items removed from newly found items.")
    return df_new_items, removed_items


def from_bib_to_csv(diag_bib_raw):
    """Convert bib file to a csv."""
    bib_data = []
    bib_columns = ['bibkey', 'type', 'title', 'authors', 'doi', 'gs_citations', 'journal', 'year', 'all_ss_ids', 'pmid']
    
    for bib_entry in diag_bib_raw:
        if bib_entry.type == 'string':
            continue

        bibkey = bib_entry.key
        bib_type = bib_entry.type
        fields = bib_entry.fields
        
        bib_authors = fields.get('author', '').strip('{}')
        bib_title = fields.get('title', '').strip('{}')
        bib_doi = fields.get('doi', '').strip('{}')
        bib_gscites = fields.get('gscites', '').strip('{}')
        bib_journal = fields.get('journal', '').strip('{}')
        bib_year = fields.get('year', '').strip('{}')
        bib_all_ss_ids = fields.get('all_ss_ids', '').strip('{}')
        bib_pmid = fields.get('pmid', '').strip('{}')
        
        bib_data.append([bibkey, bib_type, bib_title, bib_authors, bib_doi, bib_gscites, bib_journal, bib_year, bib_all_ss_ids, bib_pmid])

    df_bib_data = pd.DataFrame(bib_data, columns=bib_columns)
    return df_bib_data


def find_new_ssids(staff_id_dict, staff_year_dict):
    """
    Find new items from Semantic Scholar, based on staff IDs and years.
    Returns a DataFrame with all paper info for staff members.
    """
    all_staff_data = []
    staff_items = [(name, staff_id_dict[name], staff_year_dict[name]) for name in staff_id_dict]

    staff_dict = {key: {'ids': staff_id_dict[key], 'years': staff_year_dict[key]} for key in staff_id_dict}
    all_staff_id_ss_data = []

    for idx, (staff_name, values) in enumerate(staff_dict.items()):
        staff_ids = values['ids']
        staff_start = values['years']['start']
        staff_end = values['years']['end']
        print(f'[{idx + 1}/{len(staff_id_dict)}]: {staff_name}')

        for staff_id in staff_ids:
            print('\t\t', staff_id)
            staff_id_ss_data = []

            url = f'https://api.semanticscholar.org/graph/v1/author/{staff_id}/papers?fields=year,title,authors,externalIds,citationCount,publicationTypes,journal&limit=500'
            r = requests.get(url)
            ss_staff_data = r.json().get('data', [])

            for ss_staff_entry in ss_staff_data:
                ss_id = ss_staff_entry.get('paperId')
                ss_title = ss_staff_entry.get('title')
                ss_doi = ss_staff_entry['externalIds'].get('DOI')
                ss_citations = ss_staff_entry.get('citationCount')
                ss_year = ss_staff_entry.get('year')
                pmid = ss_staff_entry['externalIds'].get('PubMed')
                authors = ' and '.join([author['name'] for author in ss_staff_entry.get('authors', [])])
                ss_journal = ss_staff_entry.get('journal', None)
                ss_journal_name = ss_journal.get('name') if ss_journal else None
                        
                if ss_year != None:
                    ss_year = int(ss_year)
                    if not staff_start <= ss_year <= staff_end:
                    # probably doesnt belong to DIAG, still captured via another staff member if also in the same paper
                        continue
                staff_id_ss_data.append([staff_id, staff_name, staff_start, staff_end, ss_year, ss_id, ss_title, ss_doi, ss_citations, pmid, authors, ss_journal_name])
                
            all_staff_id_ss_data.extend(staff_id_ss_data)

    ss_columns = ['staff_id', 'staff_name', 'staff_from', 'staff_till', 'ss_year', 'ss_id', 'title', 'doi', 'ss_citations', 'pmid', 'authors', 'journal']
    df_all_staff_id_ss_data = pd.DataFrame(all_staff_id_ss_data, columns=ss_columns)

    print('DONE')
    return df_all_staff_id_ss_data


def find_doi_match(df_bib, df_found_items, found_items, found_dois, actions_list):
    """Find DOI matches between the bib items and found items."""
    
    list_doi_match = []
    not_new = []
    ss_id_match = []
    update_item = []
    update_item_ssid = []
    all_dois = return_existing_dois(df_bib)
    for index, row in df_bib.iterrows():
        doi = row.iloc[4]
        ss_ids = row.iloc[8]
        all_ss_ids = []
        if ss_ids is not None:
            all_ss_ids = ss_ids.split(',')
            for i, el in enumerate(all_ss_ids):
                all_ss_ids[i] = el.translate(str.maketrans('', '', string.punctuation)).strip()
        
        # Check if any existing bib-item has the same ss_id as an item on found_items 
        for ss_id in all_ss_ids:
            if ss_id in found_items:
                ss_doi = df_found_items[df_found_items['ss_id'] == ss_id]['doi'].item()
                if ss_doi:
                    ss_doi = normalize_doi(ss_doi)
                    doi = normalize_doi(doi)
                    if ss_doi != doi and ss_doi not in all_dois and (doi == '' or 'arxiv' in doi):
                        update_item.append((row.iloc[0], ss_id, f'https://www.semanticscholar.org/paper/{ss_id}', 1, doi, ss_doi, row.iloc[2], df_found_items[df_found_items['ss_id']==ss_id]['title'].item(), df_found_items[df_found_items['ss_id']==ss_id]['staff_id'].item(), df_found_items[df_found_items['ss_id']==ss_id]['staff_name'].item(), row.iloc[3], df_found_items[df_found_items['ss_id']==ss_id]['authors'].item(), row.iloc[6], df_found_items[df_found_items['ss_id']==ss_id]['journal'].item(), row.iloc[7], df_found_items[df_found_items['ss_id']==ss_id]['ss_year'].item(), row.iloc[1], df_found_items[df_found_items['ss_id']==ss_id]['pmid'].item(), 'update item', actions_list))
                        update_item_ssid.append(ss_id)
                    else:
                        not_new.append(ss_id)
                else:
                    not_new.append(ss_id)
            
        # Check if any existing bib-item has the same doi as an item on found_items
        if doi is not None and doi in found_dois:
            idx = found_dois.index(doi)
            ss_id = found_items[idx]
            # Check if that bib-item is already linked with the ss_id
            if ss_id not in all_ss_ids:
                pmid=df_found_items[df_found_items['ss_id'] ==ss_id]['pmid'].item()
                ss_title=df_found_items[df_found_items['ss_id']==ss_id]['title'].item()
                ss_authors = df_found_items[df_found_items['ss_id']==ss_id]['authors'].item()
                ss_journal_name = df_found_items[df_found_items['ss_id']==ss_id]['journal'].item()
                ss_year = int(df_found_items[df_found_items['ss_id']==ss_id]['ss_year'].item())
                staff_id = int(df_found_items[df_found_items['ss_id']==ss_id]['staff_id'].item())
                staff_name = df_found_items[df_found_items['ss_id']==ss_id]['staff_name'].item()
                ratio = SequenceMatcher(a=ss_title,b=row.iloc[2]).ratio()
                ss_id_match.append(ss_id)
                list_doi_match.append((row.iloc[0], ss_id, 'https://www.semanticscholar.org/paper/'+ss_id, ratio, doi, doi, row.iloc[2], ss_title, staff_id, staff_name, row.iloc[3], ss_authors, row.iloc[6], ss_journal_name, row.iloc[7], ss_year, row.iloc[1], pmid, 'doi match', actions_list))
    return not_new, ss_id_match, list_doi_match, update_item, update_item_ssid


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
    diag_bib_raw = read_bibfile(None, CONFIG['bib_path'])
    df_bib = from_bib_to_csv(diag_bib_raw)
    
    # Find items from semantic scholar
    df_found_items = find_new_ssids(staff_id_dict, staff_year_dict)
    # Remove duplicates and items prior to 2015
    df_found_items = df_found_items.drop_duplicates(subset=['ss_id'])
    df_found_items = df_found_items[df_found_items['ss_year']>=2015]
    found_items = df_found_items['ss_id'].tolist()
    found_dois = df_found_items['doi'].tolist()
    
    # Extract ss_ids from the bib file
    actions_list = '[add ss_id, blacklist ss_id, add new item, add manually, update_item, None]'
    # Find DOI matches
    not_new, ss_id_match, list_doi_match, update_item, update_item_ssid = find_doi_match(df_bib, df_found_items, found_items, found_dois, actions_list)
    # Remove ss_ids that are already in bibfile and ss_id with doi match
    to_add = set(found_items)-set(not_new)-set(ss_id_match)-set(update_item_ssid)
    new_items = df_found_items[df_found_items['ss_id'].isin(to_add)]
    
    # Remove blacklist items
    blacklist_path = CONFIG['output_dir'] / 'blacklist.csv'
    blacklist = pd.read_csv(blacklist_path)
    # Normalize DOIs for both new_items and blacklist before filtering
    normalized_blacklist_dois = set(normalize_doi(str(doi)) for doi in blacklist['doi'].dropna().unique())
    new_items = new_items[~new_items['doi'].apply(lambda x: normalize_doi(str(x)) if pd.notna(x) else '').isin(normalized_blacklist_dois)]
    
    # Find title matches, items without dois, new items
    list_title_match, list_no_dois, list_to_add = find_title_match_or_new_items(new_items, df_bib, actions_list)
    total_list = list_to_add + list_no_dois + list_title_match + list_doi_match + update_item

    # Save manual check file
    columns = ['bibkey', 'ss_id', 'url', 'match score', 'bib_doi', 'ss_doi', 'bib_title', 'ss_title', 'staff_id', 'staff_name', 'bib_authors', 'ss_authors', 'bib_journal', 'ss_journal', 'bib_year', 'ss_year', 'bib_type', 'ss_pmid', 'reason', 'action']
    df=pd.DataFrame(total_list, columns=columns)

    # TODO: Save .csv instead of .xlsx
    # TODO: Define folder structure in config file/ above in the script
    df_new_items, removed_items = remove_blacklist_items(df, blacklist_path)
    save_excel(removed_items, CONFIG['retrieved_items_blacklisted_path'])
    save_excel(df_new_items, CONFIG['manual_check_path'], sort_by=['ss_id'])


if __name__ == "__main__":
    main()