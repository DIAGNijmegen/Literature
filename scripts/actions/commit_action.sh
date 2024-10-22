#!/bin/bash

if [[ $(git diff) ]]; then
  git config --local user.name "action-bot"
  git add diag.bib cara.bib diagnoweb.bib fullstrings.bib medlinestrings.bib
  git commit -m "Clean up by action bot"
  echo "Committed changes"
else
  echo "Nothing changed, so did not commit"
fi
