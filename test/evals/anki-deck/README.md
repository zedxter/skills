# anki-deck evals

## Triggers

`prompts.json` has 12 prompts. Seven must load the skill:

- cards from lecture notes
- a two-way vocabulary deck from a CSV
- a Russian request for a deck from an article
- cloze cards for AnkiDroid
- rebuilding a deck without duplicates
- spaced-repetition cards from a transcript
- what is inside an `.apkg`

Five must not:

- quizzing the user in chat
- AnkiConnect code
- a summary of spaced-repetition research
- Quizlet sharing
- a printable study sheet, which is `pdf-typeset`'s job

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill anki-deck
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill anki-deck
```

Results from 2026-10-30 are in `results/2026-10-30/`.

| Agent | Correct |
|---|---|
| Claude Sonnet | 12/12 |
| Codex | 12/12 |

The first description scored the same, 12/12 on both. The only later change added "printable study sheets (pdf-typeset)" to the "Not for" list.

## Opens in Anki

`test/test_anki_deck.py` imports built decks into a fresh collection with the `anki` Python package. That package is the import code Anki desktop runs. The tests check:

- notes, cards, decks and media after the import
- importing the same deck again adds nothing
- a fixed answer updates the note instead of duplicating it, for basic and for cloze notes
- reversed notes give two cards
- cloze notes give one card per deletion
