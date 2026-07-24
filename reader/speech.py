"""Text preparation and voice discovery — carried over from the Linux version.

Everything in this module is pure Python with no OS dependency, so it is a
verbatim port of read-aloud.py's cleaning/chunking/voice code. It was tuned
against real Piper output (code blocks and hashes make the neural voice
"bubble", long unbroken utterances make it garble), so it is deliberately kept
byte-for-byte identical rather than tidied up; reader/tests/test_parity.py
diffs it against the original on nasty inputs.

The only Windows-side change is that discover_voices() takes the voices
directory as an argument instead of reading a module global, because the config
lets the user point at a voices folder outside the repo (voice files are large
and people keep them on another drive).
"""

import json
import os
import re
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
VOICES_DIR = os.path.join(HERE, "voices")

PREFERRED_VOICE = "en_US-ryan-high"

# ---------------------------------------------------------------------------
# Speech cleaning  (mirrors the logic that worked well in testing)
# ---------------------------------------------------------------------------

ABBREV = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc",
    "e.g", "i.e", "fig", "no", "vol", "pp", "al", "inc", "ltd", "co",
    "dept", "est", "min", "max", "approx", "ca", "cf", "ed", "eds",
}

CTRL_RE = re.compile(
    "[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F\u200B-\u200F\u2028\u2029\uFEFF]"
)
URL_RE = re.compile(r"\bhttps?://[^\s)]+", re.I)
WWW_RE = re.compile(r"\bwww\.[^\s)]+", re.I)
EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
RULE_RE = re.compile(r"[=_*~`#>|^]{2,}")
DASHES_RE = re.compile(r"-{2,}")
TOKEN_RE = re.compile(r"\S+")
CODEY_RE = re.compile(r"[_(){}\[\]<>/\\=+*&|^~`@#]|::|->|=>")
KEEP_PUNCT = set("'.,!?;:%$-")


def _has_letter_or_digit(s):
    return any(c.isalpha() or c.isdigit() for c in s)


def clean_token(tok):
    # Long alnum mash (hash / id / base64) — Latin-specific by design; a
    # Georgian/Cyrillic block wouldn't match either regex.
    ascii_alnum = re.sub(r"[^A-Za-z0-9]", "", tok)
    if len(ascii_alnum) >= 15 and re.search(r"[0-9]", ascii_alnum) and re.search(r"[A-Za-z]", ascii_alnum):
        return ""
    if re.match(r"^[A-Za-z0-9_\-]{30,}$", tok):
        return ""

    # Count "bad" chars: anything that isn't a Unicode letter/digit or an
    # allowed punctuation mark. Georgian/Cyrillic/CJK letters count as
    # letters here, so they no longer trip is_codey.
    symbol_count = sum(
        1 for c in tok
        if not (c.isalpha() or c.isdigit()) and c not in KEEP_PUNCT
    )
    is_codey = (
        bool(CODEY_RE.search(tok))
        or bool(re.search(r"[a-z][A-Z]", tok))          # camelCase
        or bool(re.search(r"[A-Z]{2,}[a-z]", tok))      # HTMLParser
        or symbol_count >= 2
    )
    if not is_codey:
        return tok

    t = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", tok)
    t = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", t)
    t = re.sub(r"_+", " ", t)
    t = re.sub(r"\.", " ", t)
    # Strip anything that's not a letter (ANY script), digit, or space.
    t = "".join(c if (c.isalpha() or c.isdigit() or c.isspace()) else " " for c in t)
    t = re.sub(r"\s+", " ", t).strip()
    if not _has_letter_or_digit(t):
        return ""
    if re.match(r"^[A-Za-z0-9]{25,}$", re.sub(r"\s", "", t)):
        return ""
    return t


def clean_for_speech(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = CTRL_RE.sub("", s)
    s = URL_RE.sub(" ", s)
    s = WWW_RE.sub(" ", s)
    s = EMAIL_RE.sub(
        lambda m: re.sub(r"[._%+-]", " ", m.group(1)) + " at " + re.sub(r"\.", " dot ", m.group(2)),
        s,
    )
    s = RULE_RE.sub(" ", s)
    s = DASHES_RE.sub(" ", s)
    s = TOKEN_RE.sub(lambda m: clean_token(m.group(0)), s)
    s = s.replace("&", " and ")
    s = re.sub(r"[{}<>\[\]\\|`~^]", " ", s)
    s = re.sub(r"\s*/\s*", " ", s)
    s = re.sub(r"(^|\s)[!?;:,.]+(?=\s|$)", r"\1", s)  # space-isolated punctuation
    s = re.sub(r"\s+", " ", s).strip()
    if not _has_letter_or_digit(s):
        return ""
    return s


# ---------------------------------------------------------------------------
# Chunking: keep each utterance short so Piper's model stays stable.
# A single huge utterance (e.g. a long selection with no sentence breaks)
# makes the neural voice garble / "bubble". We split on sentences + newlines
# and cap length, then feed Piper one chunk per line.
# ---------------------------------------------------------------------------

MAX_CHUNK = 160


def split_sentences(text):
    out = []
    n = len(text)
    start = 0
    i = 0
    while i < n:
        c = text[i]
        if c in ".!?":
            j = i
            while j + 1 < n and text[j + 1] in ".!?":
                j += 1
            nxt = text[j + 1] if j + 1 < n else ""
            if nxt == "" or nxt.isspace() or nxt in ')]"\u201D':
                if c == "." and i > 0 and text[i - 1].isdigit() and nxt.isdigit():
                    i = j + 1
                    continue  # decimal like 3.14
                if c == ".":
                    m = re.search(r"([A-Za-z.]+)$", text[:i])
                    if m:
                        w = m.group(1).lower().rstrip(".")
                        if w in ABBREV or len(w) == 1:
                            i = j + 1
                            continue  # abbreviation
                seg = text[start:j + 1].strip()
                if seg:
                    out.append(seg)
                start = j + 1
                i = j + 1
                continue
        i += 1
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def break_long(s, max_len):
    s = s.strip()
    if len(s) <= max_len:
        return [s] if s else []
    out = []
    while len(s) > max_len:
        window = s[:max_len]
        cut = -1
        for m in re.finditer(r"[,;:)\]]\s|\s", window):
            cut = m.end()
        if cut <= 0:
            cut = max_len
        piece = s[:cut].strip()
        if piece:
            out.append(piece)
        s = s[cut:]
    s = s.strip()
    if s:
        out.append(s)
    return out


def prepare_text(raw):
    """Split raw text into bounded, sentence-sized chunks, clean each, and
    return them newline-joined (one Piper utterance per line)."""
    lines = []
    for block in re.split(r"\n+", raw):
        block = block.strip()
        if not block:
            continue
        for sent in split_sentences(block):
            for chunk in break_long(sent, MAX_CHUNK):
                spoken = clean_for_speech(chunk)
                if spoken:
                    lines.append(spoken)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Voices
# ---------------------------------------------------------------------------

def voices_dir(override=None):
    return os.path.abspath(override) if override else VOICES_DIR


def discover_voices(directory=None):
    """Map voice id -> {model, config, rate} for every .onnx/.onnx.json pair."""
    voices = {}
    directory = voices_dir(directory)
    if not os.path.isdir(directory):
        return voices
    for fn in sorted(os.listdir(directory)):
        if not fn.endswith(".onnx"):
            continue
        model = os.path.join(directory, fn)
        cfg = model + ".json"
        if not os.path.exists(cfg):
            continue
        vid = fn[:-len(".onnx")]
        rate = 22050
        try:
            with open(cfg, encoding="utf-8") as fh:
                rate = json.load(fh).get("audio", {}).get("sample_rate", 22050)
        except (OSError, ValueError):
            pass
        voices[vid] = {"model": model, "config": cfg, "rate": rate}
    return voices


def pick_voice(voices, requested):
    if requested and requested in voices:
        return requested
    if PREFERRED_VOICE in voices:
        return PREFERRED_VOICE
    return next(iter(voices)) if voices else None


def length_scale(speed):
    """Piper's --length_scale is the inverse of a speed multiplier."""
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        speed = 1.0
    return max(0.2, min(3.0, 1.0 / max(0.1, speed)))
