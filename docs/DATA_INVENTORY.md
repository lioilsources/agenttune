# Inventura dat (fáze 0, kroky 3–4)

Stav k 2. 11. 2026, změřeno na M2 skripty `scripts/inventory_sessions.py` a `scripts/inventory_repos.sh`. Obsahují jen počty, žádný text sessions.

## Shrnutí

- **91 sessions v 66 projektech, 2 518 lidských turnů, 23 651 tool callů** v hlavních vláknech a dalších 8 799 v 198 přepisech subagentů. Celkem 1,8 GB.
- Přepisy pokrývají jen **25. 9. – 2. 11. 2026**. `cleanupPeriodDays` v `~/.claude/settings.json` chybí, takže Claude Code maže přepisy starší než 30 dní (výchozí hodnota). **Každý den mizí data.** Nastavit hodnotu musí Olin (DevConventions nezapisuje do `~/.claude/settings.json`).
- Sessions jsou dlouhé (medián desítky turnů, jedna Director session má 629 turnů). Pro SFT je bude potřeba krájet na úseky „úkol → hotovo“, celá session se do 16k tokenů nevejde.
- **Eval kandidáti jsou dostupní:** 57 rep v Go/Flutteru má commity, které mění kód i test zároveň, celkem 376 (oracle podle §2 F1). Na 20–40 úloh to stačí s rezervou.
- Názvy z plánu: „PrincessChess“ = `Chess4Princesses`, „UGC backend“ = `UGCFactory`, repo „matriky“ neexistuje.
- Klientská repa jsou mimo allowlist; jeden z nich je 4. největší zdroj turnů. Seznam je v privátním configu, ne tady.

## Návrh výběru pro eval (k potvrzení na GATE fáze 1)

Go: AiStack, PromoClown, FineTuneGallery, status-collector, Webtoon (backend), Kiran (server). Flutter: Ol1nLLM, SwypeKids, Kiran, BioDefenseRogue, OrbitronTactics, Chess4Princesses. Holdout 30 % po repech, ne po commitech, aby se kód holdoutu neobjevil v datech.

Tabulky po projektech jsou v privátním repu DevConventions (`docs/agenttune-data-inventory.md`), protože obsahují jména klientských rep.
