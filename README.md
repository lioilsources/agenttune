# agenttune

Evoluční smyčka pro lokální coding model na SPARKu: eval → data → LoRA trénink → eval → povýšení. Zadání: `DevConventions/Prompts/02-AGENTTUNE_PLAN.md`.

**Veřejné repo** (`docs/DECISIONS.md` R3). Přepisy sessions, jejich výňatky, surová data a modely do gitu nepatří (`.gitignore`).

- `docs/DATA_INVENTORY.md` — kolik je dat a které repo se hodí na eval.
- `docs/ENV.md` — co na SPARKu jede (zatím blokováno, chybí profil v brokeru).
- `docs/DECISIONS.md` — rozpory s DevConventions a rozhodnutí.
- `evals/` — úlohy, postup a kalibrace pravidel; `cmd/evalctl` a `internal/` jsou eval harness v Go.
- `scripts/` — inventura a kalibrace, běží na M2 a jen čtou.
