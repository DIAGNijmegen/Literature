import pandas as pd
import os
import string
import sys
import re
current_script_directory = os.path.dirname(os.path.realpath(__file__))
project_root = os.path.abspath(os.path.join(current_script_directory, os.pardir))
sys.path.append(os.path.join(project_root))
from get_biblatex import GetBiblatex
from bib_handling_code.processbib import read_bibfile
from bib_handling_code.processbib import save_to_file
from ast import literal_eval
from collections import defaultdict
from semanticscholar import SemanticScholar, SemanticScholarException


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


def get_item_to_blacklist(item): # item here is a row from the manually checked csv file
    #Add item to blacklist.csv
    move_to_blacklist = {
        'staff_id': item.get('staff_id', None),
        'staff_name': item.get('staff_id', None),
        'ss_year': item.get('ss_year', None),
        'ss_id': item.get('ss_id', None),
        'title': item.get('ss_title', None),
        'doi': item.get('ss_doi', None),
        'Should be in diag.bib': 'no',
        'Reason': item.get('Blacklist reason', None)
    }

    return move_to_blacklist


def update_blacklist_csv(blacklist_df, blacklist_entries, blacklist_out_file): #blacklist_csv is a df
    # Add all items to blacklist.csv
    blacklist_df = pd.concat([blacklist_df, pd.DataFrame(blacklist_entries)], ignore_index=True)

    # Save blacklist.csv
    blacklist_df.to_csv(blacklist_out_file, index=False)
    return f"{len(blacklist_entries)} items added to blacklist"


# Code to get citations from semantic scholar. If there are multiple ss_ids, we should get the number of citations for each of them and sum the two (or more?) values.
def get_citations(semantic_scholar_ids, sch):
    dict_cits = {}
    ss_ids_not_found = []
    for ss_id in semantic_scholar_ids:
        tries = 8
        i=0
        while i<tries:
            print('trying time', i, ss_id)
            try:
                paper = sch.get_paper(ss_id)
                paper_id = paper['paperId']
                dict_cits[paper_id] = len(paper['citations'])
                print('success getting citations')
                i=tries # we succeeded so max out the tries
            except SemanticScholarException.ObjectNotFoundException as onfe:
                ss_ids_not_found.append(ss_id)
                print('failed cleanly to get citations')
                i=tries # we failed cleanly so max out the tries
            except Exception as e: # some kind of time out error
                print('failed to get citations, trying again')
                i = i+1  # if we still have more tries left then try it again
            
    return dict_cits, ss_ids_not_found


def get_bib_info(diag_bib_file, item): #diag_bib_file is the file read in as a string, item is row from csv
    """
    Checks if a DOI exists in the list of BibEntry objects. If not, generates and returns a new BibTeX entry.

    Args:
        diag_bib_entries (list): List of existing DiagBib objects.
        item (dict): Row from the CSV file containing 'ss_doi' and 'ss_id'.

    Returns:
        dict or None: Returns a new BibTeX entry as a dictionary if it doesn't already exist; otherwise, returns None.
    """

    # Ensure 'ss_doi' is present in the item and is not empty or NaN
    ss_doi = str(item.get('ss_doi', ''))
    if len(ss_doi) == 0 or ss_doi.lower() == 'nan':
        print(f'No ss_doi available; cannot add new bib entry for {item.get("ss_id")}')
        return None

    # Check if the DOI already exists in the BibEntry list
    existing_entry = None
    for entry in diag_bib_file:
        # Check if the 'doi' field exists in the current BibEntry and matches 'ss_doi'
        if 'doi' in entry.fields and entry.fields['doi'] == ss_doi:
            existing_entry = entry
            break

    # If the DOI is found in the existing entries, print information and return None
    if existing_entry:
        print(f'DOI already exists in bib file. Matching item key: {existing_entry.key}')
        print(f'doi already exists in bib file, I will not add new bib entry for {ss_doi} and {item.get("ss_id")}')
        return None

    # Get BibLatex information based on DOI if not in the file
    reader = GetBiblatex(doi=item['ss_doi'], ss_id=item['ss_id'], diag_bib=diag_bib_file)
    bibtext = reader.get_bib_text()
    # Return the bibtext if it is not 'empty', otherwise return None
    return bibtext if bibtext != 'empty' else None


def add_ss_id_doi_pmid_to_existing_bibkey(diag_bib_raw, item_row):
    ss_id = item_row['ss_id']
    bibkey = item_row['bibkey']
    #Update bibkey with ss_id
    for ind, entry in enumerate(diag_bib_raw):
        if entry.type == 'string':
            continue

        # if we found the relevant key
        if bibkey == entry.key:
            # print('entry matched is ', entry.fields)
            # if there is already something in all_ss_ids
            if 'all_ss_ids' in entry.fields.keys():
                if not entry.fields['all_ss_ids'] == '{' + str(ss_id) + '}': # this should never happen, right? (from Keelin!)
                    try:
                        previous = literal_eval(entry.fields['all_ss_ids'].strip('{}'))
                    except:
                        previous = entry.fields['all_ss_ids'].strip('{}')
                        previous_list = [previous]
                        previous = [item.strip('[]') for item in previous_list]  
                    new = ss_id
                    combined = list(set(previous) | set([new]))
                    # update the entry
                    entry.fields['all_ss_ids'] = '{' + str(combined) + '}'
            # if there is no ss_id here yet just add this single one
            else:   
                    entry.fields['all_ss_ids'] = '{' + str(ss_id) + '}'
            print(str(ss_id), 'added to diag_bib_raw')

            ss_doi = str(item_row['ss_doi']).strip()
            if not 'doi' in entry.fields.keys() and len(ss_doi)>0:
                print('will add doi to bibkey', bibkey,  ss_doi)
                entry.fields['doi'] = '{' + ss_doi + '}'
            ss_pmid = item_row['ss_pmid'].strip()
            if not 'pmid' in entry.fields.keys() and len(ss_pmid)>0:
                print('will add pmid to bibkey', bibkey,  ss_pmid)
                entry.fields['pmid'] = '{' + ss_pmid + '}'


            return [diag_bib_raw, 'Success']
        
    # if we haven't returned by now then we failed to update 
    print('failed to add ss_id to diag.bib', str(ss_id), str(bibkey))
    return [diag_bib_raw, 'Fail']


def add_pmid_where_possible(diag_bib_raw, dict_bibkey_pmid):
    # iterate through all items in the diag bib and update them if we have missing information on them
    for ind, entry in enumerate(diag_bib_raw):
        if entry.type == 'string':
            continue

        # if we found the relevant key
        current_bibkey = entry.key
        if current_bibkey in dict_bibkey_pmid.keys():
            if not 'pmid' in entry.fields.keys() and len(dict_bibkey_pmid[current_bibkey].strip())>0:
                print('will add pmid to bibkey', current_bibkey,  dict_bibkey_pmid[current_bibkey].strip())
                entry.fields['pmid'] = '{' + dict_bibkey_pmid[current_bibkey].strip() + '}'

    return diag_bib_raw


def update_citation_count(diag_bib_raw):
    all_ss_ids_not_found = []
    num_entries = len(diag_bib_raw)

    sch = SemanticScholar(timeout=40)
    sch.timeout=40
    for ind, entry in enumerate(diag_bib_raw):
        # print('checking citations', ind, 'of', num_entries)
        flag=0
        if entry.type == 'string':
            continue
        if 'all_ss_ids' in entry.fields:
            all_ss_ids = []
            ss_ids = entry.fields['all_ss_ids'].translate(str.maketrans('', '', string.punctuation)).split(' ')
            if len(ss_ids) > 1:
                all_ss_ids.extend(ss_ids)
            else:
                all_ss_ids.append(ss_ids[0])
            print('trying with key', entry.key, 'and ss ids', all_ss_ids)
            dict_cits, ss_ids_not_found_this_item = get_citations(all_ss_ids, sch)
            if len(ss_ids_not_found_this_item)>0:
                print('adding items to ss_ids_not_found', ss_ids_not_found_this_item)
                all_ss_ids_not_found.extend(ss_ids_not_found_this_item)
            n_cits = 0
            for key in dict_cits.keys():
                n_cits += dict_cits[key]
            print('n_cits this item is ', n_cits)

            if 'gscites' in entry.fields:
                # only update if we are increasing the number of citations!!!
                previous_cits = int(entry.fields['gscites'].strip('{}'))
                if n_cits > previous_cits:
                    print('updating', entry.key, 'from', previous_cits, 'to', n_cits)
                    entry.fields['gscites'] = '{' + str(n_cits) + '}'
                elif (previous_cits > (1.5 * n_cits)) and (previous_cits - n_cits > 10):
                    print('warning: num citations calculated for this bibkey is much lower than previously suggested....', entry.key, previous_cits, n_cits)
                else:
                    print('will not update', entry.key, 'as there is no increase', n_cits, previous_cits)
            else:
                print('adding gscites', entry.key, n_cits)
                entry.fields['gscites'] = '{' + str(n_cits) + '}'
    print('done updating citations')
    return diag_bib_raw, all_ss_ids_not_found


def get_latest_manual_check_file(directory):
    files = [f for f in os.listdir(directory) if re.match(r'manual_check_\d{8}\.xlsx', f)]
    
    if not files:
        return None  # No matching files found
    
    # Extract dates from filenames and find the latest one
    dates = [int(re.search(r'\d{8}', f).group()) for f in files]
    latest_date = max(dates)
    
    # Build the filename of the latest file
    latest_filename = f'manual_check_{latest_date}.xlsx'
    
    return os.path.join(directory, latest_filename)


def loop_manual_check(manually_checked, diag_bib_orig):
    # Iterate through all items in the manually checked csv
    blacklist_items = []
    items_to_add = ''
    items_to_update = []
    
    failed_new_items = []
    failed_updated_items = []
    failed_to_find_actions = []
    
    dict_new_items_bibkey_pmid = {}
    
    
    for index, bib_item in manually_checked.iterrows():
        print(f"Working on {index}/{len(manually_checked)}: {bib_item['ss_doi']} (action is {bib_item['action']})")
        # Make sure item is manually checked
        if "," in bib_item['action']:
            print(f"{bib_item['ss_id']} has not been checked yet, make sure only 1 action is mentioned")
            failed_to_find_actions.append(bib_item)
            continue
    
        # Add new item to diag.bib
        elif "[add new item]" == bib_item['action'].strip() or "[update item]" == bib_item['action'].strip():
           
           bib_item_text = get_bib_info(diag_bib_orig, bib_item)
           print(bib_item_text)
           if bib_item_text is not None:
               items_to_add += bib_item_text
               # if there is a pmid note it to be added afterwards
               ss_pmid = bib_item['ss_pmid'].strip()
               if len(ss_pmid)>0:
                   # bit of a hacky way to get the bibkey of the added item
                   bibkey_added = bib_item_text[bib_item_text.index('{')+1:bib_item_text.index(',')]
                   dict_new_items_bibkey_pmid[bibkey_added] = ss_pmid
                   print('storing bibkey and pmid', bibkey_added, ss_pmid)
           else:
               print('failed to find details for doi, ss_id', bib_item['ss_doi'], bib_item['ss_id'])
               failed_new_items.append(bib_item)
           
    
        # Add ss_id to already existing doi in diag.bib
        elif "[add ss_id]" in bib_item['action'].strip():
            # just store a list of these items for now and we will update the file at the end
            items_to_update += [bib_item]
            
        # Get items to blacklist
        elif "blacklist" in bib_item['action'].strip():
            blacklist_item = get_item_to_blacklist(bib_item)
            blacklist_items.append(blacklist_item)
    
        # Get None items
        elif '[None]' in bib_item['action'].strip() or '[update item]' in bib_item['action'].strip():
            continue
            
        else:
            print('failed to find action', bib_item['action'])
            failed_to_find_actions.append(bib_item)

    return blacklist_items, items_to_add, items_to_update, failed_new_items, failed_updated_items, failed_to_find_actions, dict_new_items_bibkey_pmid


def main():
    # load manually_checked
    directory = os.path.join(project_root, 'script_data')
    filename = get_latest_manual_check_file(directory)
    manually_checked = pd.read_excel(os.path.join(directory, filename))
    print("Filename: ", filename)
    manually_checked['ss_pmid'] = manually_checked['ss_pmid'].fillna('-1')
    manually_checked['ss_pmid'] = manually_checked['ss_pmid'].astype(int).astype(str)
    manually_checked['ss_pmid'] = manually_checked['ss_pmid'].replace('-1', '')
    
    manually_checked['ss_doi'] = manually_checked['ss_doi'].fillna('')
    
    

    # load bib file just for reading at this point
    diag_bib_path = os.path.join(r"C:\Users\drepeeters\OneDrive - Radboudumc\Desktop\webteam\Literature\diag.bib")

    diag_bib_raw = read_bibfile(None, diag_bib_path)
    remove_items = manually_checked[manually_checked['action']=='[update item]']['bibkey'].tolist()
    diag_bib_raw = [entry for entry in diag_bib_raw if entry.key not in remove_items]
    save_to_file(diag_bib_raw, None, 'diag.bib')


    blacklist_items, items_to_add, items_to_update, failed_new_items, failed_updated_items, failed_to_find_actions, dict_new_items_bibkey_pmid = loop_manual_check(manually_checked, diag_bib_raw)

    with open(diag_bib_path, 'r', encoding="utf8") as orig_bib_file:
        diag_bib_orig = orig_bib_file.read()

    #Add new bib entries to the diag.bib file
    diag_bib_added_items = diag_bib_orig + items_to_add
    with open('diag.bib', 'w', encoding="utf8") as bibtex_file:
        bibtex_file.write(diag_bib_added_items)

    # # Update newly added items with pmids where possible
    diag_bib_raw = read_bibfile(None, 'diag.bib')
    diag_bib_raw = add_pmid_where_possible(diag_bib_raw, dict_new_items_bibkey_pmid)

    #Update existing bib entries with new ss_ids (and dois, pmids where possible)
    for item_to_update in items_to_update:
        [diag_bib_raw, result] = add_ss_id_doi_pmid_to_existing_bibkey(diag_bib_raw, item_to_update)
        if(result=='Fail'):
            failed_updated_items.append(item_to_update)

    # Update citation counts
    diag_bib_raw_new_cits, ss_ids_not_found_for_citations = update_citation_count(diag_bib_raw)
    save_to_file(diag_bib_raw_new_cits, None, 'diag.bib')

    # Update the blacklist
    blacklist_path = os.path.join(project_root, 'script_data', 'blacklist.csv')
    blacklist_df = pd.read_csv(blacklist_path)
    update_blacklist_csv(blacklist_df, blacklist_items, blacklist_path)

    # Here we provide a report of rows where we did not know what to do or we failed to do the action
    print("DONE with processing manually checked items")
    print('Failures are as follows:')
    for item in failed_new_items:
        print('Failed to add new bib entry ', item['ss_id'])
    for item in failed_updated_items:
        print('Failed to update exiting bib entry with new ss_id', item['bibkey'], item['ss_id'])
    for item in failed_to_find_actions:
        print('Failed to find valid action for item', item['ss_id'], item['action'])
    for item in ss_ids_not_found_for_citations:
        print('Failed to find this ss_id to update citations', item)

    print(f"Blacklisted items: {len(blacklist_items)}")
    print(f"Updated items: {len(items_to_update)}")
    print(f"Newly added items: {items_to_add.count('{yes}')}")
    import numpy as np
    count_action_none = np.sum(np.fromiter(('none' in str(action).lower() for action in manually_checked['action']), dtype=bool))
    print(f"Items with action None: {count_action_none}")
    
    
    print(f"total processed items: {len(blacklist_items) + len(items_to_update) + items_to_add.count('{yes}') + len(failed_new_items) + len(failed_updated_items) + len(failed_to_find_actions) + count_action_none}")
    print(f"amount of items in manual checkfile: {manually_checked.shape[0]}")

    save_to_file(diag_bib_raw_new_cits, None, 'diag.bib')


if __name__ == "__main__":
    main()

    

    

    
    





