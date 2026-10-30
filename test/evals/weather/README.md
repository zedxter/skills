# weather evals

## Triggers

`prompts.json` has 12 prompts. Seven must load the skill:

- the weather now
- an umbrella question for tomorrow
- a Russian weekend forecast with a "do I need a jacket"
- weather on a past date
- air quality before a run
- daily expectations for a trip
- an hourly forecast for coordinates in Fahrenheit

Five must not:

- climate science
- a React forecast component
- a unit conversion
- the Köppen classification
- a poem about rain

```bash
python3 -I test/evals/human-writing/run_triggers.py claude OUT --model sonnet --skill weather
python3 -I test/evals/human-writing/run_triggers.py codex OUT --clean --skill weather
```

Results from 2026-10-30 are in `results/2026-10-30/`.

| Description | Claude Sonnet | Codex |
|---|---|---|
| First ("Gets weather for any place …") | 12/12 | 11/12 |
| Final ("Gets weather and air quality …", plus air-quality triggers) | **12/12** | **12/12** |

With the first description, Codex answered the air-quality prompt ("Is the air quality in Beijing bad today? Should I run outside?") with a web search. The final description fixes this. It names air quality in its first line, and it adds triggers for "whether the air is clean or safe to run or go outside, AQI, PM2.5, smog or pollen today".
