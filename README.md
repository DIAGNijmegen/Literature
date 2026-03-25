# Literature created or cited at DIAG
```
    ┌────────────────────────────┐
    │ Semantic Scholar Items     │
    └───────────┬────────────────┘
                ↓
        ┌───────────────────┐
        │ In bib by ss_id?  │
        └───────┬───────────┘
                │
     No         │   Yes
      ↓         │    ↓
DOI in bib?     │   DOI same? 
   ↓            │   ↓
Yes → DOI_MATCH │   Yes → IGNORE 
No  → NEW_ITEM  │   No 
                │   ↓   
                │   Check DOI in    
                │   DIAG.bib       
                │   ↓              
                │   Match → DOI_MATCH
                │   No match
                │   ↓  
                │   Bib DOI empty or 'arxiv' → UPDATE_ITEM
                │   Not empty/ arxiv
                │   ↓  
                │   Check SS DOI in diag.bib
                │   ↓              
                │   Match → DOI_MATCH
                │   else → NEW_ITEM
```
```
    ┌────────────────────────────┐
    │ NEW ITEMS SEMANTIC SCHOLAR │
    └───────────┬────────────────┘
                ↓
     ┌────────────────────────┐
     │ Is there a title match │
     └──────────┬─────────────┘
        Yes     │        No
        ↓       │         ↓
  TITLE MATCH   │   DOI in Semantc scholar item
                │     ↓
                │   Yes → DOI_NONE
                │   No  → NEW_ITEM
```

[add ss_id, blacklist ss_id, add new item, add manually, update_item, None]
doi match,  doi none, new item, title match, update item 3

For details, please check https://repos.diagnijmegen.nl/trac/wiki/Publications%20and%20References
