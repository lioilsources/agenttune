# agenttune

Evoluční smyčka pro lokální coding model na SPARKu: eval → data → LoRA trénink → eval → povýšení. Zadání: `DevConventions/Prompts/02-AGENTTUNE_PLAN.md`.

**Privátní.** Obsahuje odvozeniny přepisů sessions. Surová data a modely nejsou v gitu (`.gitignore`).

- `docs/DATA_INVENTORY.md` — kolik je dat a které repo se hodí na eval.
- `docs/ENV.md` — co na SPARKu jede (zatím blokováno, chybí profil v brokeru).
- `docs/DECISIONS.md` — rozpory s DevConventions a rozhodnutí.
- `scripts/` — inventarizační skripty, běží na M2 a jen čtou.
