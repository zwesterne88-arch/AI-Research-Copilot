"""PDF layout cleanup for reading/translation only; never changes source offsets."""
import re

VERSION='pdf-prose-v1'
COMPOUND_PREFIXES={'single','multi','time','signal','state','end','well','semi','non','pre','post','data','in','on','off','long','short','high','low','cross','real','self','case','one','two','three','vehicle','privacy'}

def standalone_formula(text):
    """Standalone equations are source notation, including Unicode math letters."""
    return bool(re.search(r'[=∝≈≤≥]',text)) and len(re.findall(r'\b[A-Za-z]{3,}\b',text))<3

def reading_prose(text):
    text=text.replace('\u00ad','').translate(str.maketrans({'ﬁ':'fi','ﬂ':'fl','ﬀ':'ff','ﬃ':'ffi','ﬄ':'ffl'}))
    def wrap(match):
        left,right=match.groups()
        # Keep compound/algorithm hyphens, digits and single-letter variables.
        hyphen='-' if left.lower() in COMPOUND_PREFIXES or len(left)==1 or not left.islower() or not right[0].islower() else ''
        return left+hyphen+right
    text=re.sub(r'([A-Za-z]+)-[ \t]*\r?\n[ \t]*([A-Za-z]+)',wrap,text)
    text=re.sub(r'(?m)^W[ \t]+e\b','We',text)
    return '\n\n'.join(re.sub(r'\s+',' ',p).strip() for p in re.split(r'\n[ \t]*\n',text) if p.strip())

def request_segments(text,limit=1000,byte_limit=None):
    """Transport only: retain every character, prefer complete sentences/clauses."""
    out=[];a=0
    while a<len(text):
        b=min(len(text),a+limit)
        if byte_limit:
            while len(text[a:b].encode())>byte_limit:b-=1
        if b<len(text):
            choices=[a+m.end() for m in re.finditer(r'[.!?。！？]\s+',text[a:b])]
            if not choices:choices=[a+m.end() for m in re.finditer(r'[,;:]\s+|\s+',text[a:b])]
            if choices:b=choices[-1]
        out.append(text[a:b]);a=b
    return out
