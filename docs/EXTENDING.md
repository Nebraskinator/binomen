# Adding an authority

Adding a source should be a contained change: implement one protocol, register
it, done. Nothing else in the package needs to know.

## The contract

```python
from binomen.authorities.base import AuthorityResult, register
from binomen.codes import Code, normalize_status
from binomen.models import Provenance

class WoRMS:
    name = "worms"
    tier = 3
    codes = (Code.ICZN,)          # which codes this source has jurisdiction over
    license_note = "CC BY 4.0"
    redistributable = True
    homepage = "https://www.marinespecies.org/"

    @property
    def configured(self) -> bool:
        return True                # or check for credentials / a local file

    def lookup(self, name: str, *, fuzzy: bool = False) -> AuthorityResult:
        ...

register(WoRMS())
```

Then import it in `binomen/authorities/__init__.py` so registration happens.

## Four rules

**1. Never raise on failure.** Return `AuthorityResult(found=False, error=...)`.
A resolver that crashes when one of four sources is unreachable is worse than
one that reports three and names the fourth as not consulted — and an agent that
sees a crash falls back to its own memory, which is the failure we are preventing.

**2. Report your own answer, not a reconciled one.** Do not check what other
authorities said. Reconciliation happens once, in `Resolver`, where the
disagreement can be reported. An authority that pre-reconciles has destroyed the
information before anyone could see it.

**3. Pass the native status term through verbatim.** Use
`normalize_status("yoursource", native_term)` and add your vocabulary to
`_VOCABULARIES` in `codes.py`. Unmapped terms normalize to `UNKNOWN` with the
native term preserved — that is correct behavior, not a bug to paper over. Do
not force an unfamiliar term onto the nearest enum member.

**4. Fill in real provenance.** `version` should be the source's own release
identifier if it has one (an MSL number, a backbone build, a record modification
date), and `retrieved` is when *we* fetched it. Both matter: the first says what
the source asserted, the second says how stale our copy might be. If the source
has no version concept, say so in the string rather than inventing one.

## Declaring jurisdiction

`codes` is the set of codes for which this authority should be consulted.
Listing `Code.UNDETERMINED` means "consult me when the governing code could not
be determined" — it does *not* mean "I cover everything." GBIF is broad but
should not be asked about viruses, so it lists ICNP, ICNafp, ICZN and
UNDETERMINED, not ICTV.

## Licensing

Set `redistributable = False` for any source whose terms do not clearly permit
shipping derived data, and keep it query-and-cite. Check before you assume:
several taxonomic authorities are freely queryable and explicitly not freely
redistributable, and `list_authorities` surfaces this to callers so they can
report it.

## Caching

Use `binomen.authorities._http.get_json`, which routes through the on-disk cache
and honours `BINOMEN_OFFLINE=1`. Eval runs set `BINOMEN_OFFLINE=1` by default:
a number produced against a live API is not reproducible.

`get_json` raises `LookupError` when offline with no cached entry, and callers
must translate that into `error=...` rather than `found=False` with no
explanation. "The source says no such name" and "we could not reach the source"
are different facts, and collapsing them is the same class of silent failure the
project is about.

## Adding a new code

If your source governs something outside the four codes plus HGNC:

1. Add a member to `Code` in `codes.py`
2. Add a `CODE_DESCRIPTIONS` entry explaining what makes the code *different* —
   not what it covers, but what rule it has that the others do not
3. Add its native status vocabulary to `_VOCABULARIES`
4. Add lineage anchors to `detect_code` if it is detectable from a lineage
5. Add a test to `tests/test_codes.py` asserting its vocabulary differs from the
   others — that assertion is the four-codes argument in executable form
