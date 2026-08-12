"""Generate a small synthetic taxdump for offline tests.

IMPORTANT: this fixture is SYNTHETIC. The taxids are plausible but are not
guaranteed to match real NCBI taxids, and the tree is a hand-pruned skeleton.
It exists so the test suite runs without a 1 GB download and without network
access. Never use it to answer a real question, and never treat a number in
it as a citable identifier.

WARNING, LEARNED THE HARD WAY: a hand-written fixture tests the parser against
its author's beliefs about the data. Two normalization bugs shipped because
those beliefs were wrong -- NCBI's misplacement brackets ("[Clostridium]
difficile") and embedded author citations ("Clostridium difficile (Hall and
O'Toole 1935) Prevot 1938 (Approved Lists 1980)") were both absent here and
present in the archive. The name STRINGS below have since been corrected
against taxdump-2026-08-11. When adding cases, prefer real rows:

    binomen-build-index --taxdump taxdump.tar.gz --extract-fixture DIR

which pulls actual archive rows for the names in tests/fixtures/seeds.txt.

The taxa chosen mirror the case categories in eval/cases: genus reassignment,
a genus split, a homonym, a distractor pair, a multi-hop change, a contested
split, and one representative per nomenclatural code.
"""
from __future__ import annotations

import sys
from pathlib import Path

OUT = Path(__file__).parent / "taxdump"

# (taxid, parent, rank)
NODES = [
    (1, 1, "no rank"),
    (131567, 1, "no rank"),          # cellular organisms
    (10239, 1, "superkingdom"),      # Viruses  -> ICTV

    (2, 131567, "superkingdom"),     # Bacteria -> ICNP
    (2759, 131567, "superkingdom"),  # Eukaryota

    # --- Bacteria ---------------------------------------------------------
    (1239, 2, "phylum"),             # Bacillota (formerly Firmicutes)
    (186801, 1239, "class"),         # Clostridia
    (186802, 186801, "order"),       # Eubacteriales
    (186804, 186802, "family"),      # Peptostreptococcaceae
    (1870884, 186804, "genus"),      # Clostridioides
    (1496, 1870884, "species"),      # Clostridioides difficile
    (1485, 186802, "genus"),         # Clostridium
    (1502, 1485, "species"),         # Clostridium perfringens
    (2763009, 186802, "genus"),      # Mediterraneibacter
    (2763010, 2763009, "species"),   # Mediterraneibacter gnavus  -- bracket-only synonym  (distractor: stayed put)

    (91061, 1239, "class"),          # Bacilli
    (186826, 91061, "order"),        # Lactobacillales
    (33958, 186826, "family"),       # Lactobacillaceae
    (1578, 33958, "genus"),          # Lactobacillus (post-2020, much reduced)
    (1580, 1578, "species"),         # Lactobacillus delbrueckii (stayed in Lactobacillus)
    (2767841, 33958, "genus"),       # Lacticaseibacillus
    (1582, 2767841, "species"),      # Lacticaseibacillus casei
    (2767887, 33958, "genus"),       # Lactiplantibacillus
    (1590, 2767887, "species"),      # Lactiplantibacillus plantarum
    (2742598, 33958, "genus"),       # Limosilactobacillus
    (1598, 2742598, "species"),      # Limosilactobacillus reuteri

    (1385, 91061, "order"),          # Bacillales
    (186817, 1385, "family"),        # Bacillaceae
    (1386, 186817, "genus"),         # Bacillus <bacteria>   -- homonym
    (1423, 1386, "species"),         # Bacillus subtilis

    (1224, 2, "phylum"),             # Pseudomonadota (formerly Proteobacteria)
    (1236, 1224, "class"),           # Gammaproteobacteria
    (91347, 1236, "order"),          # Enterobacterales
    (543, 91347, "family"),          # Enterobacteriaceae
    (570, 543, "genus"),             # Klebsiella
    (548, 570, "species"),           # Klebsiella aerogenes
    (547, 543, "genus"),             # Enterobacter
    (550, 547, "species"),           # Enterobacter cloacae  (distractor)
    (561, 543, "genus"),             # Escherichia
    (562, 561, "species"),           # Escherichia coli      (control)

    # --- Fungi (ICNafp) ---------------------------------------------------
    (33154, 2759, "clade"),          # Opisthokonta
    (4751, 33154, "kingdom"),        # Fungi
    (4890, 4751, "phylum"),          # Ascomycota
    (147537, 4890, "class"),         # Saccharomycetes
    (4892, 147537, "order"),         # Saccharomycetales
    (1573264, 4892, "family"),       # Metschnikowiaceae
    (3132688, 1573264, "genus"),     # Candidozyma       -- contested
    (498019, 3132688, "species"),    # Candidozyma auris
    (1535326, 4892, "genus"),        # Candida
    (5476, 1535326, "species"),      # Candida albicans  (distractor: not moved)
    (4894, 4890, "class"),           # Schizosaccharomycetes-ish placeholder
    (4753, 4890, "genus"),           # Pneumocystis
    (42068, 4753, "species"),        # Pneumocystis jirovecii  (human)
    (4754, 4753, "species"),         # Pneumocystis carinii    (rat) -- VERY MAJOR distractor

    # --- Plants (ICNafp) --------------------------------------------------
    (33090, 2759, "kingdom"),        # Viridiplantae
    (4447, 33090, "clade"),          # Liliopsida-ish placeholder
    (4210, 33090, "family"),         # Asteraceae
    (13334, 4210, "genus"),          # Symphyotrichum
    (1421077, 13334, "species"),     # Symphyotrichum novae-angliae
    (13333, 4210, "genus"),          # Aster
    (13335, 13333, "species"),       # Aster amellus  (stayed in Aster)

    # --- Animals (ICZN) ---------------------------------------------------
    (33208, 33154, "kingdom"),       # Metazoa
    (7711, 33208, "phylum"),         # Chordata
    (40674, 7711, "class"),          # Mammalia
    (9443, 40674, "order"),          # Primates
    (9605, 9443, "genus"),           # Homo
    (9606, 9605, "species"),         # Homo sapiens (control)
    (8782, 7711, "class"),           # Aves
    (216573, 8782, "genus"),         # Cyanistes
    (156563, 216573, "species"),     # Cyanistes caeruleus
    (9152, 8782, "genus"),           # Parus
    (9157, 9152, "species"),         # Parus major  (distractor: stayed in Parus)
    (6656, 33208, "phylum"),         # Arthropoda
    (7041, 6656, "order"),           # Coleoptera-ish placeholder
    (55087, 6656, "genus"),          # Bacillus <stick insect> -- HOMONYM

    # --- Viruses (ICTV) ---------------------------------------------------
    (2732005, 10239, "phylum"),      # Uroviricota-ish placeholder
    (11118, 10239, "family"),        # Coronaviridae
    (694002, 11118, "genus"),        # Betacoronavirus
    (694009, 694002, "species"),     # SARS-related coronavirus
    (2697049, 694009, "no rank"),    # SARS-CoV-2
    (10292, 10239, "family"),        # Herpesviridae
    (10294, 10292, "genus"),         # Simplexvirus
    (10298, 10294, "species"),       # Human alphaherpesvirus 1
]

# (taxid, name, unique_name, name_class)
NAMES = [
    (1, "root", "", "scientific name"),
    (131567, "cellular organisms", "", "scientific name"),
    (10239, "Viruses", "", "scientific name"),
    (2, "Bacteria", "Bacteria <bacteria>", "scientific name"),
    (2, "eubacteria", "", "genbank common name"),
    (2759, "Eukaryota", "", "scientific name"),

    # Phylum-level renames -- entire phyla were renamed in 2021 under ICNP
    # when phylum names became subject to the Code.
    (1239, "Bacillota", "", "scientific name"),
    (1239, "Firmicutes", "", "synonym"),
    (1224, "Pseudomonadota", "", "scientific name"),
    (1224, "Proteobacteria", "", "synonym"),

    (186801, "Clostridia", "", "scientific name"),
    (186802, "Eubacteriales", "", "scientific name"),
    (186802, "Clostridiales", "", "synonym"),
    (186804, "Peptostreptococcaceae", "", "scientific name"),

    # Multi-hop: Bacillus difficilis -> Clostridium difficile ->
    # Peptoclostridium difficile -> Clostridioides difficile
    (1870884, "Clostridioides", "", "scientific name"),
    (1496, "Clostridioides difficile", "", "scientific name"),
    # NOTE the shape of these strings. NCBI stores prokaryote synonyms as full
    # nomenclatural citations, not bare binomials -- verified against
    # taxdump-2026-08-11. A user types "Clostridium difficile"; the archive
    # contains the line below. The hand-written fixture originally had the bare
    # form, which is why the suite passed while the real index silently
    # returned "unknown" for the project's own headline example.
    (1496, "Clostridium difficile (Hall and O'Toole 1935) Prevot 1938 (Approved Lists 1980)",
     "", "synonym"),
    (1496, "Peptoclostridium difficile (Hall and O'Toole 1935) Yutin and Galperin 2013",
     "", "synonym"),
    (1496, "Bacillus difficilis Hall and O'Toole 1935", "", "synonym"),
    (1496, "[Clostridium] difficile", "", "equivalent name"),
    (1496, "C. difficile", "", "genbank common name"),
    (1496, "(Hall and O'Toole 1935) Lawson et al. 2016", "", "authority"),
    (1485, "Clostridium", "", "scientific name"),
    # Bracket-only: NCBI carries no plain "Ruminococcus gnavus" for this taxon,
    # only the bracketed form flagging the misplacement. A user will type the
    # plain binomial, so the normalizer has to fold the brackets or the lookup
    # silently misses. This is the real shape of the bug the canary caught.
    (2763009, "Mediterraneibacter", "", "scientific name"),
    (2763010, "Mediterraneibacter gnavus", "", "scientific name"),
    (2763010, "[Ruminococcus] gnavus", "", "equivalent name"),
    (1502, "Clostridium perfringens", "", "scientific name"),

    (91061, "Bacilli", "", "scientific name"),
    (186826, "Lactobacillales", "", "scientific name"),
    (33958, "Lactobacillaceae", "", "scientific name"),
    (1578, "Lactobacillus", "", "scientific name"),
    (1580, "Lactobacillus delbrueckii", "", "scientific name"),
    (2767841, "Lacticaseibacillus", "", "scientific name"),
    (1582, "Lacticaseibacillus casei", "", "scientific name"),
    (1582, "Lactobacillus casei", "", "synonym"),
    (2767887, "Lactiplantibacillus", "", "scientific name"),
    (1590, "Lactiplantibacillus plantarum", "", "scientific name"),
    (1590, "Lactobacillus plantarum", "", "synonym"),
    (2742598, "Limosilactobacillus", "", "scientific name"),
    (1598, "Limosilactobacillus reuteri", "", "scientific name"),
    (1598, "Lactobacillus reuteri", "", "synonym"),

    (1385, "Bacillales", "", "scientific name"),
    (186817, "Bacillaceae", "", "scientific name"),
    (1386, "Bacillus", "Bacillus <bacteria>", "scientific name"),
    (1423, "Bacillus subtilis", "", "scientific name"),

    (1236, "Gammaproteobacteria", "", "scientific name"),
    (91347, "Enterobacterales", "", "scientific name"),
    (543, "Enterobacteriaceae", "", "scientific name"),
    (570, "Klebsiella", "", "scientific name"),
    (548, "Klebsiella aerogenes", "", "scientific name"),
    (548, "Enterobacter aerogenes Hormaeche and Edwards 1960 (Approved Lists 1980)",
     "", "synonym"),
    (548, "Aerobacter aerogenes", "", "synonym"),
    (547, "Enterobacter", "", "scientific name"),
    (550, "Enterobacter cloacae", "", "scientific name"),
    (561, "Escherichia", "", "scientific name"),
    (562, "Escherichia coli", "", "scientific name"),
    (562, "E. coli", "", "genbank common name"),

    (33154, "Opisthokonta", "", "scientific name"),
    (4751, "Fungi", "", "scientific name"),
    (4890, "Ascomycota", "", "scientific name"),
    (147537, "Saccharomycetes", "", "scientific name"),
    (4892, "Saccharomycetales", "", "scientific name"),
    (1573264, "Metschnikowiaceae", "", "scientific name"),
    (3132688, "Candidozyma", "", "scientific name"),
    (498019, "Candidozyma auris", "", "scientific name"),
    (498019, "Candida auris", "", "synonym"),
    (498019, "[Candida] auris", "", "equivalent name"),
    (1535326, "Candida", "Candida <fungus>", "scientific name"),
    (5476, "Candida albicans", "", "scientific name"),
    (4894, "Schizosaccharomycetes", "", "scientific name"),
    (4753, "Pneumocystis", "", "scientific name"),
    (42068, "Pneumocystis jirovecii", "", "scientific name"),
    (42068, "Pneumocystis carinii f. sp. hominis", "", "synonym"),
    (42068, "Pneumocystis jiroveci", "", "misspelling"),
    (4754, "Pneumocystis carinii", "", "scientific name"),

    (33090, "Viridiplantae", "", "scientific name"),
    (4447, "Liliopsida", "", "scientific name"),
    (4210, "Asteraceae", "", "scientific name"),
    (13334, "Symphyotrichum", "", "scientific name"),
    (1421077, "Symphyotrichum novae-angliae", "", "scientific name"),
    (1421077, "Aster novae-angliae", "", "synonym"),
    (13333, "Aster", "Aster <plant>", "scientific name"),
    (13335, "Aster amellus", "", "scientific name"),

    (33208, "Metazoa", "", "scientific name"),
    (33208, "Animalia", "", "synonym"),
    (7711, "Chordata", "", "scientific name"),
    (40674, "Mammalia", "", "scientific name"),
    (9443, "Primates", "", "scientific name"),
    (9605, "Homo", "", "scientific name"),
    (9606, "Homo sapiens", "", "scientific name"),
    (9606, "Homo sapiens Linnaeus, 1758", "", "authority"),
    (9606, "human", "", "genbank common name"),
    (8782, "Aves", "", "scientific name"),
    (216573, "Cyanistes", "", "scientific name"),
    (156563, "Cyanistes caeruleus", "", "scientific name"),
    (156563, "Parus caeruleus", "", "synonym"),
    (156563, "blue tit", "", "genbank common name"),
    (9152, "Parus", "", "scientific name"),
    (9157, "Parus major", "", "scientific name"),
    (6656, "Arthropoda", "", "scientific name"),
    (7041, "Phasmatodea", "", "scientific name"),
    (55087, "Bacillus", "Bacillus <stick insect>", "scientific name"),

    (2732005, "Uroviricota", "", "scientific name"),
    (11118, "Coronaviridae", "", "scientific name"),
    (694002, "Betacoronavirus", "", "scientific name"),
    (694009, "Severe acute respiratory syndrome-related coronavirus", "", "scientific name"),
    (694009, "SARS-related coronavirus", "", "equivalent name"),
    (2697049, "Severe acute respiratory syndrome coronavirus 2", "", "scientific name"),
    (2697049, "SARS-CoV-2", "", "genbank common name"),
    (2697049, "2019-nCoV", "", "synonym"),
    (10292, "Herpesviridae", "", "scientific name"),
    (10294, "Simplexvirus", "", "scientific name"),
    (10298, "Human alphaherpesvirus 1", "", "scientific name"),
    (10298, "Human herpesvirus 1", "", "synonym"),
    (10298, "Herpes simplex virus 1", "", "equivalent name"),
    (10298, "HHV-1", "", "acronym"),
]

# (old_taxid, new_taxid) -- NCBI's record that two taxa were unified.
MERGED = [
    (1428020, 1496),     # a former Peptoclostridium difficile node
    (1219073, 548),      # a former Enterobacter aerogenes node
    (1046628, 42068),    # a former P. carinii f. sp. hominis node
    (2743101, 498019),   # a former Candida auris node
]

DELETED = [9999991, 9999992]


def field_line(parts) -> str:
    return "\t|\t".join(str(p) for p in parts) + "\t|\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    marker = OUT / "PROVENANCE.txt"
    if marker.exists():
        if "--force" not in sys.argv:
            print(f"{OUT} holds a REAL extracted taxdump; leaving it alone.\n"
                  f"{marker.read_text().splitlines()[1]}\n"
                  f"Pass --force to overwrite with the synthetic fixture.")
            return 0
        # Overwriting real rows with synthetic ones invalidates the marker. If
        # it survives, the directory claims a provenance it no longer has --
        # a stale label on changed data, which is the entire subject of this
        # project and not a thing to leave lying around in its own test suite.
        try:
            marker.unlink()
            print(f"--force: removed {marker.name}; this fixture is synthetic again.")
        except OSError as e:
            print(f"WARNING: could not remove {marker} ({e}).\n"
                  f"         The directory now claims a provenance it does not have. "
                  f"Delete that file by hand.")
    with open(OUT / "nodes.dmp", "w", encoding="utf-8") as f:
        for taxid, parent, rank in NODES:
            # taxid | parent | rank | embl | division id | ...
            f.write(field_line([taxid, parent, rank, "", "0", "1", "1", "1", "0", "1", "1", "0", ""]))
    with open(OUT / "names.dmp", "w", encoding="utf-8") as f:
        for taxid, name, uniq, klass in NAMES:
            f.write(field_line([taxid, name, uniq, klass]))
    with open(OUT / "merged.dmp", "w", encoding="utf-8") as f:
        f.writelines(field_line([old, new]) for old, new in MERGED)
    with open(OUT / "delnodes.dmp", "w", encoding="utf-8") as f:
        f.writelines(field_line([t]) for t in DELETED)
    print(f"wrote fixture taxdump to {OUT} "
          f"({len(NODES)} nodes, {len(NAMES)} names, {len(MERGED)} merges)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
