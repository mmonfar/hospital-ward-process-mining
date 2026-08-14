# SPEC-000 — <title>

**Status:** draft | accepted | superseded · **Node:** N?? · **Owner role:** architect|builder
**Depends on:** SPEC-??? · **Last revised:** YYYY-MM-DD

## Problem

What is being solved, in the language of `01-DOMAIN-MODEL.md`. One paragraph.

## In scope / out of scope

Explicit boundaries. The out-of-scope list is the more useful half — it is what
stops a later session quietly widening the work.

## Interface

Public types and functions. Signatures, not implementations. This is the contract
other specs may depend on.

## Modelling assumptions

Every choice that could reasonably have been made differently, with the reason
and the parameter that controls it. In health data science this section is what
makes a result defensible; anything here that is stated as fact rather than
assumption is a defect.

## Acceptance criteria

Numbered, individually testable, each with a named test. "Works correctly" is not
an acceptance criterion.

1. ...

## Test oracle

How we know the answer is right — ground truth from the synthetic generator, a
brute-forced small instance, a known-optimum benchmark, or clinician review.
State which. If there is no oracle, say so; that is a finding, not an omission.

## Failure modes

What is most likely to go wrong and how it would be detected. Written before
implementation, when it is still honest.

## Open questions

Unresolved items, each marked as blocking or non-blocking, and who decides.
