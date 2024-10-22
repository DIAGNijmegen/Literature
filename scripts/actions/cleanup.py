"""
This script will contain automated features to clean up the repository. It currently has the following functionalities:
- Remove spaces at the end of the lines in bib files. The website cannot read files that contain spaces at the end
  of lines.
"""

import os


def remove_eol_spaces_bib():
    """
    Removes spaces at the end of lines in bib files.
    """
    bib_files = [
        "diag.bib",
        "cara.bib", 
        "diagnoweb.bib",
        "fullstrings.bib",
        "medlinestrings.bib",
    ]

    for bib_file in bib_files:
        out_lines = []
        was_updated = False
        with open(bib_file, "r") as f:
            lines = f.readlines()
            for line_idx, line in enumerate(lines):
                rstripped = line.rstrip() + "\n"
                out_lines.append(rstripped)

                if rstripped != line:
                    n_spaces = len(line) - len(rstripped)
                    print(f"{bib_file}: Line {line_idx} contained {n_spaces} spaces at the end.")
                    print("This is the line's content:", line)
                    print("The space at the end of the line was removed.")

                    was_updated = True

        if was_updated:
            with open(bib_file, "w") as f:
                f.writelines(out_lines)


if __name__ == '__main__':
    remove_eol_spaces_bib()
