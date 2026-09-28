"""engine/mo.py — MO (Modus Operandi) similarity computation.

MO similarity is supporting evidence only and can never create a link
by itself (Rule 3). It is computed within crime-type groups using
character n-gram TF-IDF over de-identified narrative tokens.

Exports
-------
mo_tokens(text) -> list[str]
    Strip phone numbers, UPI IDs, vehicle regs, amounts and location
    references from a narrative, then return lower-case alpha tokens.

mo_similarity(firs) -> dict[(fir_id_a, fir_id_b), float]
    Compute pairwise cosine similarity within each crime-type group.
    Pairs across different crime types are not computed (would always be
    irrelevant and would waste memory on a 200-FIR dataset).
"""
import collections
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from ingest.rule_extract import PHONE_RX, UPI_RX, VREG_RX


def mo_tokens(text: str) -> list[str]:
    """Return de-identified lower-case alpha tokens from a narrative string.

    Strips phone numbers, UPI IDs, vehicle registrations, numeric amounts
    and "near <locality>" phrases before tokenising so that shared identifiers
    do not inflate MO similarity scores.
    """
    t = PHONE_RX.sub(" ", text)
    t = UPI_RX.sub(" ", t)
    t = VREG_RX.sub(" ", t)
    t = re.sub(r"\d[\d,\-]*", " ", t.lower())
    t = re.sub(r"near\s+\w+(\s\w+)?", " ", t)
    return re.findall(r"[a-z]+", t)


def mo_similarity(firs: list[dict]) -> dict[tuple[str, str], float]:
    """Compute pairwise MO cosine similarity for FIRs within each crime type.

    Parameters
    ----------
    firs : list[dict]
        Normalised FIR dicts.  Each must have keys 'id', 'crime_type', 'narrative'.

    Returns
    -------
    dict mapping (fir_id_a, fir_id_b) -> cosine similarity float.
    Both orderings are stored, including self-pairs.
    """
    sim: dict[tuple[str, str], float] = {}
    by: dict[str, list[dict]] = collections.defaultdict(list)

    for f in firs:
        by[f["crime_type"]].append(f)

    for fs in by.values():
        tokens = [" ".join(mo_tokens(f["narrative"])) for f in fs]
        X = TfidfVectorizer(ngram_range=(3, 5), sublinear_tf=True).fit_transform(tokens)
        S = cosine_similarity(X)
        for i, a in enumerate(fs):
            for j, b in enumerate(fs):
                sim[(a["id"], b["id"])] = float(S[i, j])

    return sim
