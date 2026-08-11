from binomen.codes import Code, Status, detect_code, normalize_status, status_vocabulary_for


def test_detects_each_code():
    assert detect_code(["cellular organisms", "Bacteria"]).code is Code.ICNP
    assert detect_code(["Viruses", "Riboviria"]).code is Code.ICTV
    assert detect_code(["Eukaryota", "Metazoa", "Chordata"]).code is Code.ICZN
    assert detect_code(["Eukaryota", "Fungi"]).code is Code.ICNAFP
    assert detect_code(["Eukaryota", "Viridiplantae"]).code is Code.ICNAFP
    assert detect_code([], is_gene=True).code is Code.HGNC


def test_dual_claimed_groups_are_not_guessed():
    """Protists are claimed by more than one code. Guessing would be a fabrication."""
    a = detect_code(["Eukaryota", "Sar", "Alveolata", "Apicomplexa"])
    assert a.code is Code.UNDETERMINED
    assert Code.ICZN in a.alternatives and Code.ICNAFP in a.alternatives


def test_empty_lineage_is_undetermined_not_a_default():
    assert detect_code([]).code is Code.UNDETERMINED


def test_native_term_is_never_lost():
    s = normalize_status("lpsn", "not validly published")
    assert s.normalized is Status.NOT_VALIDLY_PUBLISHED
    assert s.native == "not validly published"


def test_unknown_term_passes_through_rather_than_being_forced():
    s = normalize_status("gbif", "SOMETHING_NEW")
    assert s.normalized is Status.UNKNOWN
    assert s.native == "SOMETHING_NEW"
    assert "not in binomen's vocabulary map" in (s.note or "")


def test_status_vocabularies_differ_between_codes():
    """The whole four-codes argument in one assertion."""
    icnp = status_vocabulary_for(Code.ICNP)
    icnafp = status_vocabulary_for(Code.ICNAFP)
    assert "not validly published" in icnp
    assert "nom. inval." in icnafp
    assert set(icnp) != set(icnafp)
