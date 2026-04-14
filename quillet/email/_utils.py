import re

import markdown2


def md_to_plain(md: str) -> str:
    """Convert markdown to a clean plain-text string for email text/plain parts."""
    html = markdown2.markdown(md)
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
