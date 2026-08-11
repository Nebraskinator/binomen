#!/usr/bin/env python3
"""Case set source. Emits cases.jsonl and heldout.jsonl.

Kept as Python rather than hand-written JSONL so the cases can be commented and
reviewed. Every entry ships `confidence: "unverified"` -- see SCHEMA.md. Run
eval/verify_cases.py before using any of these for a reported number.

Prompt style note: prompts are written the way a working scientist would
actually type them, not as taxonomy quiz questions. This matters. "What is the
current name of Clostridium difficile?" announces itself as a nomenclature
question and any competent agent will treat it carefully. "Summarize the
treatment options for C. difficile infection" does not announce itself, and
that is where the silent failure actually lives.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
U = "unverified"


def C(id, category, code, prompt, check, expected, tool_expected=True,
      sources=(), notes=None, holdout=False):
    d = {
        "id": id, "category": category, "code": code, "prompt": prompt,
        "check": check, "expected": expected, "tool_expected": tool_expected,
        "confidence": U, "sources": list(sources),
    }
    if notes:
        d["notes"] = notes
    if holdout:
        d["holdout"] = True
    return d


CASES = [
    # ---------------------------------------------------------------- control
    C("control-001", "control", "ICNP", "Is Escherichia coli still the accepted name for E. coli?",
      "current_name", {"accepted": "Escherichia coli"}, True, ["NCBI Taxonomy", "LPSN"],
      "Control. Should resolve cleanly with no change chain."),
    C("control-002", "control", "ICZN", "What is the currently accepted scientific name for humans?",
      "current_name", {"accepted": "Homo sapiens"}, False, ["NCBI Taxonomy"],
      "Control where a tool call is genuinely unnecessary. Included to check the tool is not "
      "invoked reflexively on every organism mention -- over-invocation has a real cost."),
    C("control-003", "control", "ICNP", "Confirm the genus of Staphylococcus aureus.",
      "current_name", {"accepted": "Staphylococcus aureus"}, True, ["LPSN"]),
    C("control-004", "control", "ICNafp", "Is Saccharomyces cerevisiae a current name?",
      "current_name", {"accepted": "Saccharomyces cerevisiae"}, True, ["MycoBank", "NCBI"]),
    C("control-005", "control", "ICZN", "Is Drosophila melanogaster still the accepted name?",
      "current_name", {"accepted": "Drosophila melanogaster"}, True, ["GBIF"],
      "Drosophila has had proposals to split the genus; the type species D. melanogaster is the "
      "one name in the genus least likely to have moved. A tool that reports instability here is "
      "over-flagging."),
    C("control-006", "control", "ICNafp", "Confirm the accepted name of Arabidopsis thaliana.",
      "current_name", {"accepted": "Arabidopsis thaliana"}, True, ["World Flora Online", "GBIF"]),
    C("control-007", "control", "ICNP", "Is Mycobacterium tuberculosis current?",
      "current_name", {"accepted": "Mycobacterium tuberculosis"}, True, ["LPSN"],
      "Some Mycobacterium species were moved to Mycolicibacterium and other segregate genera in "
      "2018, but M. tuberculosis was not. Distractor-adjacent control."),
    C("control-008", "control", "ICZN", "Is Danio rerio the accepted name for zebrafish?",
      "current_name", {"accepted": "Danio rerio"}, True, ["GBIF"]),
    C("control-009", "control", "ICNafp", "Is Aspergillus fumigatus still valid?",
      "current_name", {"accepted": "Aspergillus fumigatus"}, True, ["MycoBank"],
      "Aspergillus was at the center of the 'one fungus one name' teleomorph disputes; the "
      "species name itself was retained."),
    C("control-010", "control", "ICNP", "Confirm Pseudomonas aeruginosa.",
      "current_name", {"accepted": "Pseudomonas aeruginosa"}, True, ["LPSN"]),

    # --------------------------------------------------------------- historic
    C("historic-001", "historic", "ICNP",
      "I'm writing up a case of Clostridium difficile colitis. What should I call the organism?",
      "current_name", {"accepted": "Clostridioides difficile"}, True,
      ["Lawson et al. 2016, Anaerobe"],
      "The canonical case. Prompt is a writing task, not a taxonomy question."),
    C("historic-002", "historic", "ICNP",
      "Our lab report lists Enterobacter aerogenes. Is that the right genus?",
      "current_name", {"accepted": "Klebsiella aerogenes"}, True, ["Tindall et al. 2017, IJSEM"],
      "Clinically consequential: the genera differ in typical resistance profile."),
    C("historic-003", "historic", "ICNafp",
      "A 1990s paper describes Pneumocystis carinii pneumonia in an AIDS patient. What organism is that?",
      "current_name", {"accepted": "Pneumocystis jirovecii"}, True,
      ["Stringer et al. 2002, EID"],
      "The hard version: the answer is a different organism than the name denotes today, and "
      "P. carinii is still a valid name for the rat pathogen."),
    C("historic-004", "historic", "ICNP", "What genus is Lactobacillus casei in now?",
      "current_name", {"accepted": "Lacticaseibacillus casei"}, True, ["Zheng et al. 2020, IJSEM"]),
    C("historic-005", "historic", "ICNP", "Is Lactobacillus plantarum still called that?",
      "current_name", {"accepted": "Lactiplantibacillus plantarum"}, True, ["Zheng et al. 2020"]),
    C("historic-006", "historic", "ICZN",
      "An old European ornithology paper uses Parus caeruleus. Which bird is that and what is it called now?",
      "current_name", {"accepted": "Cyanistes caeruleus"}, True, ["IOC/Clements checklists"],
      "Bird checklists disagree on adoption timing; a good answer says which checklist."),
    C("historic-007", "historic", "ICNafp",
      "A herbarium label reads Aster novae-angliae. What is the accepted name?",
      "current_name", {"accepted": "Symphyotrichum novae-angliae"}, True,
      ["Nesom 1994, Phytologia", "World Flora Online"]),
    C("historic-008", "historic", "ICNP",
      "Is Streptococcus faecalis current?",
      "current_name", {"accepted": "Enterococcus faecalis"}, True, ["LPSN"],
      "1984 transfer. Old enough that both names are thoroughly represented in training corpora."),
    C("historic-009", "historic", "ICTV",
      "A paper refers to Human herpesvirus 1. What is the current ICTV species name?",
      "current_name", {"accepted": "Human alphaherpesvirus 1"}, True, ["ICTV MSL"],
      "Committee rename; the vernacular HSV-1 was never taxonomic and is still fine."),
    C("historic-010", "historic", "ICNP",
      "Our metagenomics pipeline reports Firmicutes. Is that the current phylum name?",
      "flags_disagreement", {"names": ["Bacillota", "Firmicutes"]}, True,
      ["Oren & Garrity 2021, IJSEM"],
      "Adoption is genuinely uneven. Asserting only Bacillota, with no note that most tooling and "
      "literature still says Firmicutes, is unhelpful even though it is nomenclaturally correct."),
    C("historic-011", "historic", "ICNP", "Is Proteobacteria still used as a phylum name?",
      "flags_disagreement", {"names": ["Pseudomonadota", "Proteobacteria"]}, True,
      ["Oren & Garrity 2021"]),
    C("historic-012", "historic", "ICNafp",
      "Is Candida glabrata still in the genus Candida?",
      "current_name", {"accepted": "Nakaseomyces glabratus",
                       "also_acceptable": ["Nakaseomyces glabrata", "Candida glabrata"]}, True,
      ["MycoBank", "clinical mycology literature"],
      "Adoption is partial in clinical settings; a good answer gives both and says which is which."),
    C("historic-013", "historic", "ICZN",
      "What is the accepted genus for the fruit bat formerly placed in Pteropus that was moved? "
      "Answer only if you can verify the specific taxon.",
      "states_unknown", {}, True, [],
      "Deliberately underspecified. The correct behavior is to ask which taxon is meant or say it "
      "cannot be determined, not to produce a plausible genus."),
    C("historic-014", "historic", "ICNP",
      "Convert this species list to current nomenclature: Clostridium difficile, Lactobacillus reuteri, "
      "Enterobacter aerogenes.",
      "must_include_terms",
      {"terms": ["Clostridioides difficile", "Limosilactobacillus reuteri", "Klebsiella aerogenes"],
       "min_fraction": 1.0}, True, ["LPSN", "Zheng et al. 2020"]),

    # ----------------------------------------------------------------- recent
    C("recent-001", "recent", "ICNafp",
      "We're drafting an infection-control notice about Candida auris. What name should we use?",
      "flags_disagreement", {"names": ["Candidozyma auris", "Candida auris"]}, True,
      ["Liu et al. 2024, Studies in Mycology", "subsequent clinical commentary"],
      "The reference contested case, in the setting where getting it wrong has a cost."),
    C("recent-002", "recent", "ICNP",
      "Has the genus Bacteroides been reorganized recently? Which species moved?",
      "states_unknown", {}, True, ["LPSN"],
      "Scored on whether the agent checks rather than recalls. Several Bacteroides species have "
      "moved to Phocaeicola and other genera; an unverified list is worse than 'let me check'."),
    C("recent-003", "recent", "ICTV",
      "What is the current ICTV species name for SARS-CoV-2, and is 'SARS-CoV-2' itself a species name?",
      "states_unknown", {}, True, ["ICTV MSL"],
      "Two traps. SARS-CoV-2 is a virus isolate name, not a species; the species has been "
      "subject to the ICTV binomial rollout. An agent that conflates virus and species has "
      "misunderstood ICTV's rank structure."),
    C("recent-004", "recent", "ICNP",
      "Is the phylum name Actinobacteria still correct?",
      "flags_disagreement", {"names": ["Actinomycetota", "Actinobacteria"]}, True,
      ["Oren & Garrity 2021"]),
    C("recent-005", "recent", "ICNafp",
      "Has anything changed recently in the naming of Cryptococcus neoformans and C. gattii?",
      "states_unknown", {}, True, ["MycoBank", "clinical mycology literature"],
      "The proposal to split the C. neoformans/gattii complex into multiple species was published "
      "and then substantially contested. Correct behavior is to flag that this is unsettled."),
    C("recent-006", "recent", "ICNP",
      "Which genus is Ruminococcus gnavus placed in now?",
      "states_unknown", {}, True, ["LPSN"],
      "Recently reassigned; likely at or past training cutoffs."),
    C("recent-007", "recent", "ICZN",
      "Have there been recent splits in the giraffe genus Giraffa?",
      "states_unknown", {}, True, ["IUCN", "mammal checklists"],
      "Genuinely unsettled: proposals for multiple giraffe species are debated. Flagging is correct."),
    C("recent-008", "recent", "ICNafp",
      "Is Candida krusei still the accepted name?",
      "current_name", {"accepted": "Pichia kudriavzevii",
                       "also_acceptable": ["Candida krusei"]}, True, ["MycoBank"],
      "Clinical labs largely still report Candida krusei. Both should appear."),
    C("recent-009", "recent", "ICNP",
      "Summarize what's known about Akkermansia muciniphila as a probiotic candidate.",
      "must_not_substitute", {"wrong": []}, False, ["LPSN"],
      "No name change. Tests over-application: an agent that has learned to flag renames may "
      "invent instability where there is none."),
    C("recent-010", "recent", "ICTV",
      "Under the ICTV binomial rollout, how should a virus species name be formatted?",
      "states_unknown", {}, True, ["ICTV"],
      "Tests whether the agent knows ICTV species names are now binomial and committee-assigned "
      "rather than descriptive, and cites an MSL rather than a year."),

    # ------------------------------------------------------------------ split
    C("split-001", "split", "ICNP",
      "Our study used Lactobacillus strains. What genus should we report now?",
      "split_disambiguation",
      {"mapping": {"Lactobacillus casei": "Lacticaseibacillus casei",
                   "Lactobacillus plantarum": "Lactiplantibacillus plantarum",
                   "Lactobacillus reuteri": "Limosilactobacillus reuteri"},
       "must_not_say": ["Lactobacillus is now Lacticaseibacillus",
                        "Lactobacillus was renamed"]}, True,
      ["Zheng et al. 2020, IJSEM"],
      "The defining split case. There is no genus-level answer. An agent that substitutes at the "
      "genus level has made a very major error even though it 'knows about' the 2020 split."),
    C("split-002", "split", "ICNP", "Does the genus Lactobacillus still exist?",
      "current_name", {"accepted": "Lactobacillus"}, True, ["Zheng et al. 2020"],
      "Yes -- it was narrowed, not abolished. 'Lactobacillus no longer exists' is a common and "
      "confident error."),
    C("split-003", "split", "ICNafp",
      "All the Aster species in my North American plot data -- do they need renaming?",
      "split_disambiguation",
      {"mapping": {"Aster novae-angliae": "Symphyotrichum novae-angliae"},
       "must_not_say": ["Aster is now Symphyotrichum"]}, True, ["Nesom 1994"],
      "Aster persists for Eurasian species. Blanket substitution corrupts the dataset."),
    C("split-004", "split", "ICNP", "Was Clostridium split up? What happened to Clostridium species?",
      "split_disambiguation",
      {"mapping": {"Clostridium difficile": "Clostridioides difficile"},
       "must_not_say": ["Clostridium is now Clostridioides"]}, True,
      ["Lawson et al. 2016", "LPSN"],
      "Clostridium sensu stricto remains and contains C. perfringens, C. botulinum and others."),
    C("split-005", "split", "ICNP",
      "Are all Mycobacterium species still in Mycobacterium?",
      "split_disambiguation",
      {"mapping": {"Mycobacterium tuberculosis": "Mycobacterium tuberculosis"},
       "must_not_say": ["Mycobacterium is now Mycolicibacterium"]}, True, ["LPSN"],
      "The 2018 proposal moved many non-tuberculous species but was itself contested; M. "
      "tuberculosis did not move."),
    C("split-006", "split", "ICZN",
      "The tit genus Parus in my dataset -- has it been split?",
      "split_disambiguation",
      {"mapping": {"Parus caeruleus": "Cyanistes caeruleus", "Parus major": "Parus major"},
       "must_not_say": ["Parus is now Cyanistes"]}, True, ["bird checklists"]),
    C("split-007", "split", "ICNafp",
      "Is every species in Candida going to be renamed?",
      "states_unknown", {}, True, ["MycoBank"],
      "Candida is known to be polyphyletic, so the answer is 'many but not all, and it is ongoing "
      "and partly contested'. A confident yes or no is wrong."),
    C("split-008", "split", "ICNP",
      "Reclassify these for a manuscript: Lactobacillus delbrueckii, Lactobacillus acidophilus, "
      "Lactobacillus casei.",
      "split_disambiguation",
      {"mapping": {"Lactobacillus delbrueckii": "Lactobacillus delbrueckii",
                   "Lactobacillus acidophilus": "Lactobacillus acidophilus",
                   "Lactobacillus casei": "Lacticaseibacillus casei"}}, True,
      ["Zheng et al. 2020"],
      "Two of the three did not move. Uniform substitution fails."),

    # ---------------------------------------------------------------- homonym
    C("homonym-001", "homonym", "n/a",
      "What organism is Bacillus? Give its classification.",
      "flags_disagreement", {"names": ["Bacillus <bacteria>", "Bacillus <stick insect>"]}, True,
      ["NCBI Taxonomy"],
      "Bacillus is a bacterial genus and a stick insect genus, under different codes. Homonymy "
      "across codes is legal -- ICZN and ICNP do not police each other."),
    C("homonym-002", "homonym", "n/a", "Tell me about the genus Prunella.",
      "flags_disagreement", {"names": ["Prunella (plant)", "Prunella (bird)"]}, True, ["GBIF"],
      "Plant genus (selfheal) and bird genus (accentors)."),
    C("homonym-003", "homonym", "n/a", "What is Oenanthe?",
      "flags_disagreement", {"names": ["Oenanthe (plant)", "Oenanthe (bird)"]}, True, ["GBIF"],
      "Water dropwort and wheatears."),
    C("homonym-004", "homonym", "n/a", "Classify the genus Morus.",
      "flags_disagreement", {"names": ["Morus (plant)", "Morus (bird)"]}, True, ["GBIF"],
      "Mulberry and gannets."),
    C("homonym-005", "homonym", "n/a", "What kind of organism is Ficus?",
      "flags_disagreement", {"names": ["Ficus (plant)", "Ficus (mollusc)"]}, True, ["GBIF"],
      "Figs and a genus of sea snails."),
    C("homonym-006", "homonym", "ICNP",
      "A dataset column contains 'Bacillus'. Can I safely join it to my bacterial reference table?",
      "states_unknown", {}, True, ["NCBI Taxonomy"],
      "Correct answer: not safely without disambiguation. This is the join-failure framing stated "
      "directly."),
    C("homonym-007", "homonym", "n/a",
      "Is the genus Aotus a plant or an animal?",
      "flags_disagreement", {"names": ["Aotus (plant)", "Aotus (monkey)"]}, True, ["GBIF"],
      "A legume genus and the night monkeys."),

    # ------------------------------------------------------------- distractor
    C("distractor-001", "distractor", "ICNafp",
      "Are Pneumocystis carinii and Pneumocystis jirovecii the same organism?",
      "same_taxon", {"same": False}, True, ["Stringer et al. 2002"],
      "Very major if answered 'yes'. Similar names, different hosts, both currently valid."),
    C("distractor-002", "distractor", "ICNP",
      "Are Bacillus subtilis and Bacillus anthracis synonyms?",
      "same_taxon", {"same": False}, True, ["LPSN"]),
    C("distractor-003", "distractor", "ICNP",
      "Klebsiella aerogenes and Klebsiella pneumoniae -- same species?",
      "same_taxon", {"same": False}, True, ["LPSN"],
      "Same genus, different species. Tests that the recent K. aerogenes transfer does not cause "
      "over-merging with the better-known congener."),
    C("distractor-004", "distractor", "ICNafp",
      "Is Candida auris the same as Candida albicans?",
      "same_taxon", {"same": False}, True, ["MycoBank"]),
    C("distractor-005", "distractor", "ICNP",
      "Are Escherichia coli and Shigella flexneri the same organism?",
      "states_unknown", {}, True, ["genomics literature"],
      "Genuinely hard and genuinely interesting: Shigella is phylogenetically nested within E. "
      "coli, but the names are retained for clinical and historical reasons. Neither a flat yes "
      "nor a flat no is right; the correct answer explains the tension."),
    C("distractor-006", "distractor", "ICZN",
      "Are Canis lupus and Canis lupus familiaris different species?",
      "same_taxon", {"same": True}, True, ["mammal checklists"],
      "Subspecies of the same species. Tests rank handling, not synonymy."),
    C("distractor-007", "distractor", "ICNP",
      "Clostridioides difficile and Clostridium perfringens -- one organism or two?",
      "same_taxon", {"same": False}, True, ["LPSN"],
      "Different genera now, same genus historically. An agent that over-applies the C. difficile "
      "rename may wrongly link them."),
    C("distractor-008", "distractor", "ICNafp",
      "Is Aspergillus fumigatus the same as Neosartorya fumigata?",
      "same_taxon", {"same": True}, True, ["MycoBank", "Melbourne Code 2011"],
      "Yes -- anamorph/teleomorph pair, unified by 'one fungus, one name'. The inverse trap: two "
      "names that look like different organisms and are not."),
    C("distractor-009", "distractor", "ICTV",
      "Are Human alphaherpesvirus 1 and Human alphaherpesvirus 2 the same species?",
      "same_taxon", {"same": False}, True, ["ICTV MSL"]),
    C("distractor-010", "distractor", "ICNP",
      "Is Lactobacillus delbrueckii the same as Lactobacillus delbrueckii subsp. bulgaricus?",
      "same_taxon", {"same": True}, True, ["LPSN"],
      "Species vs subspecies. 'Same taxon' is arguably true at species rank; a good answer states "
      "the rank rather than answering flatly."),

    # ---------------------------------------------------------------- multihop
    C("multihop-001", "multihop", "ICNP",
      "Trace the full naming history of the organism now called Clostridioides difficile.",
      "must_include_terms",
      {"terms": ["Bacillus difficilis", "Clostridium difficile", "Peptoclostridium difficile",
                 "Clostridioides difficile"], "min_fraction": 0.75}, True,
      ["Lawson et al. 2016", "LPSN"],
      "Three genus placements. The Peptoclostridium step is short-lived and usually omitted."),
    C("multihop-002", "multihop", "ICNP",
      "What has Klebsiella aerogenes been called over time?",
      "must_include_terms",
      {"terms": ["Aerobacter aerogenes", "Enterobacter aerogenes", "Klebsiella aerogenes"],
       "min_fraction": 0.66}, True, ["LPSN"]),
    C("multihop-003", "multihop", "ICNafp",
      "List every name that has been applied to the fungus now called Candidozyma auris.",
      "must_include_terms", {"terms": ["Candida auris", "Candidozyma auris"], "min_fraction": 1.0},
      True, ["Liu et al. 2024"]),
    C("multihop-004", "multihop", "ICNP",
      "Has Enterococcus faecalis had more than one genus name?",
      "must_include_terms", {"terms": ["Streptococcus faecalis", "Enterococcus faecalis"],
                             "min_fraction": 1.0}, True, ["LPSN"]),
    C("multihop-005", "multihop", "ICTV",
      "What names has the virus commonly called HSV-1 had in ICTV taxonomy?",
      "must_include_terms", {"terms": ["Human herpesvirus 1", "Human alphaherpesvirus 1"],
                             "min_fraction": 1.0}, True, ["ICTV MSL"]),
    C("multihop-006", "multihop", "ICNP",
      "Our 2014 dataset says Peptoclostridium difficile and our 2022 dataset says Clostridioides "
      "difficile. Are these the same organism, and why do we have three names in our archive?",
      "same_taxon", {"same": True}, True, ["Lawson et al. 2016"],
      "The join-failure scenario stated as a data problem rather than a naming question."),

    # ------------------------------------------------------------- literature
    C("literature-001", "literature", "ICNP",
      "Build me a PubMed query to retrieve all literature on Clostridioides difficile infection, "
      "including older work.",
      "must_include_terms",
      {"terms": ["Clostridioides difficile", "Clostridium difficile", "Peptoclostridium difficile",
                 "C. difficile"], "min_fraction": 0.75}, True, ["LPSN", "NCBI"],
      "The literature-linking use case. Omitting 'Clostridium difficile' loses decades of work "
      "and returns a result set that looks complete."),
    C("literature-002", "literature", "ICNP",
      "I need every paper on Klebsiella aerogenes for a systematic review. What search terms?",
      "must_include_terms", {"terms": ["Klebsiella aerogenes", "Enterobacter aerogenes",
                                       "Aerobacter aerogenes"], "min_fraction": 0.66}, True,
      ["LPSN"]),
    C("literature-003", "literature", "ICNafp",
      "Search terms for a review of Pneumocystis pneumonia in humans.",
      "must_include_terms", {"terms": ["Pneumocystis jirovecii", "Pneumocystis carinii"],
                             "min_fraction": 1.0}, True, ["Stringer et al. 2002"],
      "Must include P. carinii -- most of the human clinical literature is under it -- while not "
      "claiming the two names denote the same taxon today."),
    C("literature-004", "literature", "ICNP",
      "How would I find all studies on Lactobacillus reuteri as a probiotic?",
      "must_include_terms", {"terms": ["Limosilactobacillus reuteri", "Lactobacillus reuteri"],
                             "min_fraction": 1.0}, True, ["Zheng et al. 2020"]),
    C("literature-005", "literature", "ICNafp",
      "Give me search terms covering all literature on Candida auris outbreaks.",
      "must_include_terms", {"terms": ["Candida auris", "Candidozyma auris"], "min_fraction": 1.0},
      True, ["Liu et al. 2024"]),
    C("literature-006", "literature", "ICNP",
      "A meta-analysis on gut Firmicutes abundance -- what should the search string include?",
      "must_include_terms", {"terms": ["Firmicutes", "Bacillota"], "min_fraction": 1.0}, True,
      ["Oren & Garrity 2021"]),
    C("literature-007", "literature", "ICZN",
      "Retrieve everything on the blue tit's breeding ecology.",
      "must_include_terms", {"terms": ["Cyanistes caeruleus", "Parus caeruleus"],
                             "min_fraction": 1.0}, True, ["bird checklists"]),
    C("literature-008", "literature", "ICNafp",
      "Search strategy for all work on Symphyotrichum novae-angliae.",
      "must_include_terms", {"terms": ["Symphyotrichum novae-angliae", "Aster novae-angliae"],
                             "min_fraction": 1.0}, True, ["Nesom 1994"]),
    C("literature-009", "literature", "ICNP",
      "I searched PubMed for 'Clostridioides difficile' restricted to 1990-2000 and got almost "
      "nothing. Is that real?",
      "states_unknown", {}, True, ["Lawson et al. 2016"],
      "The silent failure made explicit. The near-zero result is an artifact of the 2016 rename, "
      "not a finding about the literature. An agent that accepts the empty result at face value "
      "has committed the error the whole project is about."),
    C("literature-010", "literature", "ICNafp",
      "Build a comprehensive search for Aspergillus fumigatus including its teleomorph literature.",
      "must_include_terms", {"terms": ["Aspergillus fumigatus", "Neosartorya fumigata"],
                             "min_fraction": 1.0}, True, ["Melbourne Code 2011", "MycoBank"]),

    # -------------------------------------------------------------- crosscode
    C("crosscode-001", "crosscode", "ICNP",
      "What does it mean that a bacterial name is 'not validly published'?",
      "states_unknown", {}, True, ["ICNP"],
      "ICNP-specific. A name can be published in a peer-reviewed journal and still have no "
      "standing until it appears in IJSEM or on a Validation List. No other code works this way."),
    C("crosscode-002", "crosscode", "ICNafp",
      "In 'Candidozyma auris (Satoh & Makimura) Liu et al.', what do the parentheses mean?",
      "states_unknown", {}, True, ["ICNafp"],
      "The parenthetical author is the basionym author. ICNafp encodes change history inside the "
      "name; ICZN does not use parentheses the same way."),
    C("crosscode-003", "crosscode", "ICZN",
      "Under ICZN, if a species moves to a different genus, does the author citation change?",
      "states_unknown", {}, True, ["ICZN"],
      "Tests whether the agent knows ICZN and ICNafp differ here rather than generalizing from one."),
    C("crosscode-004", "crosscode", "ICTV",
      "How do I cite the current taxonomic placement of a virus?",
      "states_unknown", {}, True, ["ICTV"],
      "Correct answer names an MSL release, not a year or an author."),
    C("crosscode-005", "crosscode", "n/a",
      "Can a bacterium and an animal have the same genus name?",
      "same_taxon", {"same": False}, True, ["ICNP", "ICZN"],
      "Yes -- the codes are independent, so cross-code homonymy is legal. Bacillus is the example."),
    C("crosscode-006", "crosscode", "ICNafp",
      "What was 'one fungus, one name' and what did it change?",
      "states_unknown", {}, True, ["Melbourne Code 2011"],
      "A rule change, not an evidence change: dual anamorph/teleomorph naming was abolished, so "
      "previously legitimate names became impermissible."),
    C("crosscode-007", "crosscode", "n/a",
      "Which code governs the naming of Plasmodium falciparum?",
      "states_unknown", {}, True, ["ICZN", "ICNafp"],
      "Apicomplexa are historically claimed by ICZN, but protist nomenclature is contested "
      "territory. 'Undetermined, consult both' is a legitimate answer."),
    C("crosscode-008", "crosscode", "ICNP",
      "Is 'Candidatus Liberibacter asiaticus' a valid name?",
      "states_unknown", {}, True, ["ICNP"],
      "Candidatus is a formal category under ICNP for organisms that cannot be cultured and "
      "therefore cannot be validly published. Neither valid nor an error."),
    C("crosscode-009", "crosscode", "ICNafp",
      "Does 'nom. illeg.' mean the same thing as 'not validly published'?",
      "same_taxon", {"same": False}, True, ["ICNafp", "ICNP"],
      "No. An illegitimate name was validly published but contravenes a rule. Flattening these is "
      "the status-vocabulary error the resolver is designed to prevent."),
    C("crosscode-010", "crosscode", "ICTV",
      "Do viruses have binomial species names like bacteria do?",
      "states_unknown", {}, True, ["ICTV"]),
    C("crosscode-011", "crosscode", "ICZN",
      "If two animal species were given the same name, which one keeps it?",
      "states_unknown", {}, True, ["ICZN"],
      "Homonymy and priority. The junior homonym must be replaced."),
    C("crosscode-012", "crosscode", "n/a",
      "I have a table mixing bacterial, fungal and viral names with a single 'status' column of "
      "'valid'/'invalid'. Is that safe?",
      "states_unknown", {}, True, ["ICNP", "ICNafp", "ICTV"],
      "No. The status vocabularies are not interchangeable. This is the four-codes argument posed "
      "as a schema-design question."),

    # ---------------------------------------------------------------- contested
    C("contested-001", "contested", "ICNafp",
      "Should our lab switch from Candida auris to Candidozyma auris?",
      "flags_disagreement", {"names": ["Candidozyma auris", "Candida auris"]}, True,
      ["Liu et al. 2024", "clinical mycology commentary"]),
    C("contested-002", "contested", "ICNP",
      "Is Bacillota or Firmicutes correct?",
      "flags_disagreement", {"names": ["Bacillota", "Firmicutes"]}, True, ["Oren & Garrity 2021"]),
    C("contested-003", "contested", "ICNP",
      "Should I write Pseudomonadota or Proteobacteria in a microbiome paper?",
      "flags_disagreement", {"names": ["Pseudomonadota", "Proteobacteria"]}, True,
      ["Oren & Garrity 2021"]),
    C("contested-004", "contested", "ICNafp",
      "Is Nakaseomyces glabratus the accepted name for what we used to call Candida glabrata?",
      "flags_disagreement", {"names": ["Nakaseomyces glabratus", "Candida glabrata"]}, True,
      ["MycoBank", "clinical mycology literature"]),
    C("contested-005", "contested", "ICNP",
      "Are the Mycolicibacterium and related segregate genera generally accepted?",
      "flags_disagreement", {"names": ["Mycolicibacterium", "Mycobacterium"]}, True, ["LPSN"],
      "The 2018 split was formally published and then publicly contested; adoption is partial."),
    C("contested-006", "contested", "ICZN",
      "How many giraffe species are there?",
      "flags_disagreement", {"names": ["one species", "multiple species"]}, True,
      ["IUCN", "mammal checklists"]),
    C("contested-007", "contested", "ICNafp",
      "Is the Cryptococcus gattii species complex one species or several?",
      "flags_disagreement", {"names": ["single species complex", "multiple species"]}, True,
      ["clinical mycology literature"]),
    C("contested-008", "contested", "n/a",
      "Give me the single correct current name for Candida auris. Just the name, no caveats.",
      "flags_disagreement", {"names": ["Candidozyma auris", "Candida auris"]}, True,
      ["Liu et al. 2024"],
      "Adversarial. The user explicitly asks for a single answer with no caveats, and complying "
      "produces false confidence. Tests whether the instruction to be concise overrides the "
      "obligation to report a real disagreement."),

    # ------------------------------------------------------------------- gene
    C("gene-001", "gene", "HGNC",
      "Our RNA-seq table has a column SEPT2. What gene is that and is the symbol current?",
      "current_name", {"accepted": "SEPTIN2"}, True, ["HGNC"],
      "Renamed in 2020 partly because spreadsheets coerced it to a date."),
    C("gene-002", "gene", "HGNC", "Is MARCH1 a current gene symbol?",
      "current_name", {"accepted": "MARCHF1"}, True, ["HGNC"]),
    C("gene-003", "gene", "HGNC",
      "My supplementary table contains '2-Sep' in a gene symbol column. What happened?",
      "states_unknown", {}, True, ["HGNC", "Ziemann et al. 2016, Genome Biology"],
      "Excel date coercion. A documented case of a file format silently corrupting the published "
      "record -- the same join-failure class as taxonomy, with a paper trail."),
    C("gene-004", "gene", "HGNC",
      "Map these to current symbols: SEPT9, MARCH5, DEC1.",
      "must_include_terms", {"terms": ["SEPTIN9", "MARCHF5"], "min_fraction": 1.0}, True, ["HGNC"],
      "DEC1 is ambiguous -- it has been used for more than one gene. Correct behavior is to "
      "resolve the two unambiguous ones and flag the third."),
    C("gene-005", "gene", "HGNC", "Is TP53 still the approved symbol?",
      "current_name", {"accepted": "TP53"}, True, ["HGNC"],
      "Control. Should not be flagged as changed."),
    C("gene-006", "gene", "HGNC",
      "I'm joining two datasets on gene symbol. What could go wrong?",
      "states_unknown", {}, True, ["HGNC"],
      "The general statement of the failure mode. Symbol drift, aliases, Excel coercion, and "
      "species-specific casing conventions."),
]

# Held out. Run once, at the end. Same category mix, different taxa.
HELDOUT = [
    C("h-control-001", "control", "ICNP", "Is Bacillus subtilis a current name?",
      "current_name", {"accepted": "Bacillus subtilis"}, True, ["LPSN"], holdout=True),
    C("h-control-002", "control", "ICZN", "Confirm the accepted name of the domestic cat.",
      "current_name", {"accepted": "Felis catus"}, True, ["mammal checklists"], holdout=True),
    C("h-control-003", "control", "ICNafp", "Is Zea mays current?",
      "current_name", {"accepted": "Zea mays"}, True, ["WFO"], holdout=True),
    C("h-historic-001", "historic", "ICNP",
      "A protocol lists Pseudomonas cepacia as the contaminant. What is that organism now?",
      "current_name", {"accepted": "Burkholderia cepacia"}, True, ["LPSN"], holdout=True),
    C("h-historic-002", "historic", "ICNP",
      "Is Bacteroides fragilis still in Bacteroides?",
      "current_name", {"accepted": "Bacteroides fragilis"}, True, ["LPSN"],
      "Type species of the genus; did not move in the Phocaeicola reorganization.", holdout=True),
    C("h-historic-003", "historic", "ICNafp",
      "An old mycology key uses Hansenula. What genus is that now?",
      "current_name", {"accepted": "Pichia", "also_acceptable": ["Ogataea", "Wickerhamomyces"]},
      True, ["MycoBank"],
      "Hansenula was dismantled across several genera; the answer depends on the species.",
      holdout=True),
    C("h-historic-004", "historic", "ICZN",
      "A paper uses Rana catesbeiana for the American bullfrog. Current name?",
      "current_name", {"accepted": "Lithobates catesbeianus",
                       "also_acceptable": ["Rana catesbeiana"]}, True, ["amphibian checklists"],
      "Genuinely contested between competing amphibian taxonomies.", holdout=True),
    C("h-recent-001", "recent", "ICNP",
      "Has the genus Prevotella been reorganized?", "states_unknown", {}, True, ["LPSN"],
      holdout=True),
    C("h-recent-002", "recent", "ICTV",
      "What is the current ICTV species for monkeypox virus and what release are you citing?",
      "states_unknown", {}, True, ["ICTV MSL"], holdout=True),
    C("h-split-001", "split", "ICNP",
      "Do all my Bacteroides isolates need renaming?", "split_disambiguation",
      {"mapping": {"Bacteroides fragilis": "Bacteroides fragilis"},
       "must_not_say": ["Bacteroides is now Phocaeicola"]}, True, ["LPSN"], holdout=True),
    C("h-split-002", "split", "ICNafp",
      "The Scilla species in my flora -- have they moved?", "states_unknown", {}, True, ["WFO"],
      "Scilla was extensively segregated; species-level answer required.", holdout=True),
    C("h-homonym-001", "homonym", "n/a", "What is the genus Arenaria?",
      "flags_disagreement", {"names": ["Arenaria (plant)", "Arenaria (bird)"]}, True, ["GBIF"],
      "Sandworts and turnstones.", holdout=True),
    C("h-homonym-002", "homonym", "n/a", "Tell me about Erica.",
      "flags_disagreement", {"names": ["Erica (plant)", "Erica (insect)"]}, True, ["GBIF"],
      holdout=True),
    C("h-distractor-001", "distractor", "ICNP",
      "Are Burkholderia cepacia and Burkholderia pseudomallei the same organism?",
      "same_taxon", {"same": False}, True, ["LPSN"], holdout=True),
    C("h-distractor-002", "distractor", "ICNafp",
      "Is Candida parapsilosis the same as Candida orthopsilosis?",
      "same_taxon", {"same": False}, True, ["MycoBank"],
      "Cryptic species split out of what was one named species.", holdout=True),
    C("h-distractor-003", "distractor", "ICZN",
      "Are Mus musculus and Mus musculus domesticus different species?",
      "same_taxon", {"same": True}, True, ["mammal checklists"], holdout=True),
    C("h-multihop-001", "multihop", "ICNP",
      "Trace the genus history of the organism now called Burkholderia cepacia.",
      "must_include_terms", {"terms": ["Pseudomonas cepacia", "Burkholderia cepacia"],
                             "min_fraction": 1.0}, True, ["LPSN"], holdout=True),
    C("h-multihop-002", "multihop", "ICNP",
      "What has Enterococcus faecium been called?",
      "must_include_terms", {"terms": ["Streptococcus faecium", "Enterococcus faecium"],
                             "min_fraction": 1.0}, True, ["LPSN"], holdout=True),
    C("h-literature-001", "literature", "ICNP",
      "Search terms for all literature on Burkholderia cepacia complex infections in cystic fibrosis.",
      "must_include_terms", {"terms": ["Burkholderia cepacia", "Pseudomonas cepacia"],
                             "min_fraction": 1.0}, True, ["LPSN"], holdout=True),
    C("h-literature-002", "literature", "ICNP",
      "Comprehensive search for Enterococcus faecalis endocarditis including older work.",
      "must_include_terms", {"terms": ["Enterococcus faecalis", "Streptococcus faecalis"],
                             "min_fraction": 1.0}, True, ["LPSN"], holdout=True),
    C("h-crosscode-001", "crosscode", "ICNP",
      "What is a Validation List and why does it exist?", "states_unknown", {}, True, ["ICNP"],
      holdout=True),
    C("h-crosscode-002", "crosscode", "ICNafp",
      "What is a basionym?", "states_unknown", {}, True, ["ICNafp"], holdout=True),
    C("h-contested-001", "contested", "ICZN",
      "How many species of African elephant are there?",
      "flags_disagreement", {"names": ["Loxodonta africana", "Loxodonta cyclotis"]}, True,
      ["IUCN"], holdout=True),
    C("h-contested-002", "contested", "ICNP",
      "Is the phylum name Bacteroidota or Bacteroidetes correct?",
      "flags_disagreement", {"names": ["Bacteroidota", "Bacteroidetes"]}, True,
      ["Oren & Garrity 2021"], holdout=True),
    C("h-gene-001", "gene", "HGNC", "Is SEPT7 a current gene symbol?",
      "current_name", {"accepted": "SEPTIN7"}, True, ["HGNC"], holdout=True),
    C("h-gene-002", "gene", "HGNC", "Is BRCA1 still approved?",
      "current_name", {"accepted": "BRCA1"}, True, ["HGNC"], holdout=True),
]


def write(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    print(f"wrote {len(rows)} cases to {path}")


def main() -> int:
    ids = [c["id"] for c in CASES + HELDOUT]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise SystemExit(f"duplicate case ids: {dupes}")
    write(HERE / "cases.jsonl", CASES)
    write(HERE / "heldout.jsonl", HELDOUT)
    from collections import Counter
    print("dev by category: ", dict(Counter(c["category"] for c in CASES)))
    print("dev by code:     ", dict(Counter(c["code"] for c in CASES)))
    print("holdout:         ", dict(Counter(c["category"] for c in HELDOUT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
