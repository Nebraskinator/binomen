"""The error taxonomy. Adapted from severity grading in antimicrobial
susceptibility testing, where a very major error (reporting a resistant isolate
as susceptible) and a minor error are not averaged together, because one of
them kills people and the other does not.

Plain accuracy is the wrong instrument for this problem for the same reason.
It scores a silent join failure and a missing citation identically, when the
first invalidates a downstream conclusion and the second merely makes it
harder to check.

Two of these classes are measured but NOT folded into the severity score:

  ABSTENTION_FAILURE co-occurs with correct answers. An agent that guesses
  right without checking has not succeeded, it has been lucky, and averaging
  it into an accuracy number hides exactly the behavior under study.

  FALSE_CONFIDENCE only applies to contested cases, where the ground truth is
  "there is no single answer". Scoring it as a correctness failure would
  penalize the same event twice.
"""

from __future__ import annotations

from enum import Enum


class ErrorClass(str, Enum):
    NONE = "none"
    VERY_MAJOR = "very_major"
    MAJOR = "major"
    MINOR = "minor"
    ABSTENTION_FAILURE = "abstention_failure"
    FALSE_CONFIDENCE = "false_confidence"


SEVERITY_WEIGHT = {
    ErrorClass.NONE: 0.0,
    ErrorClass.MINOR: 1.0,
    ErrorClass.MAJOR: 4.0,
    ErrorClass.VERY_MAJOR: 10.0,
    # Reported separately; weight 0 so they never silently enter the severity
    # score. See the module docstring.
    ErrorClass.ABSTENTION_FAILURE: 0.0,
    ErrorClass.FALSE_CONFIDENCE: 0.0,
}

DEFINITIONS = {
    ErrorClass.VERY_MAJOR: (
        "Asserted two names are different taxa when they are the same, or the same when they are "
        "different. Silent data loss or false conflation; the downstream conclusion is wrong and "
        "nothing signals it."
    ),
    ErrorClass.MAJOR: (
        "Used a superseded name as current without flagging it. Retrieval is incomplete and the "
        "results look valid."
    ),
    ErrorClass.MINOR: (
        "Correct identification, missing provenance, source, or date qualifier. Not reproducible, "
        "but not wrong."
    ),
    ErrorClass.ABSTENTION_FAILURE: (
        "Answered from parametric memory when a tool was available and needed. Measured "
        "separately because it co-occurs with correct answers."
    ),
    ErrorClass.FALSE_CONFIDENCE: (
        "Gave a single answer where authorities genuinely disagree."
    ),
}
