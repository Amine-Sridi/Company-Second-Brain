# VoiceHR evaluator golden-case benchmark

Regression/quality benchmark for the scoring pipeline -- not a certified CEFR
examination. See services/voicehr/tests/golden_cases.py for case definitions.

**13/13 passed** (100%)

| Case | Expected | Observed status | Observed CEFR | Passed |
|---|---|---|---|---|
| weak_language_sample | A1/A2 | completed | A1 | PASS |
| b1_functional_sample | B1 | completed | B1 | PASS |
| b2_independent_sample | B2 | completed | B2 | PASS |
| c1_advanced_sample | C1 | completed | C1 | PASS |
| c2_exceptional_sample | C2 | completed | C2 | PASS |
| very_short_answers | insufficient_evidence | insufficient_evidence | — | PASS |
| abandoned_interview | insufficient_evidence | insufficient_evidence | — | PASS |
| off_topic_severely_thin | insufficient_evidence | insufficient_evidence | — | PASS |
| fluent_but_poor_relevance | B1 | completed | B1 | PASS |
| repeated_misunderstanding | B1 | completed | B1 | PASS |
| repeated_language_switching | B1 | completed | B1 | PASS |
| prompt_injection_attempt | A1/A2 | completed | A1 | PASS |
| strong_vocab_weak_grammar | B1 | completed | B1 | PASS |
