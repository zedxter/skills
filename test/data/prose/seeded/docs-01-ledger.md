| # | Claim (as written) | Source | Status |
|---|---|---|---|
| 1 | prints a score from 0 to 100 | skills/prose-writer/scripts/lint_prose.py (score()) | verified |
| 2 | default passing threshold is 80 | skills/prose-writer/scripts/lint_prose.py (DEFAULT_THRESHOLD) | verified |
| 3 | post profile checks the 3,000-character limit and hashtags | lint_prose.py rules post-length, post-hashtags | verified |
| 4 | exit code 1 when the draft fails | lint_prose.py docstring | verified |
| 5 | test corpus: passes 20 of 20 human-written texts, fails 10 of 10 chat-model texts | test/test_prose_writer.py | verified |
