# Rozhodnutí a otevřené body

Zadání: `DevConventions/Prompts/02-AGENTTUNE_PLAN.md`. Platí pravidla DevConventions (`PLAN.md` §5, `shared/*.md`); kde je plán v rozporu, pravidla mají přednost, dokud Olin nerozhodne jinak.

## Rozpory plánu s DevConventions

| # | Plán říká | Pravidlo | Návrh |
|---|---|---|---|
| R1 | Fáze 0, 3, 5: Docker, trénink a týdenní cron „na Sparku“ | Na SPARKu nic nespouštět mimo broker; katalog nemá profil pro trénink ani vlastní kontejner | Přidat do brokeru profil `train` (exkluzivní GPU, vlastní image) a nechat ho spouštět brokerem přes `sparkctl run`/`exec`. Profil musíš přidat ty nebo broker session, ne tato session. |
| R2 | §2 F2: data zůstávají na Sparku, F5 hook kopíruje sessions na SPARK | PLAN §9.4: přepisy obsahují secrets, nikdy mimo M2 | Extrakce a filtr secrets na M2, na SPARK jde jen vyčištěný `sft/dpo.jsonl` |
| R3 | Repo `agenttune` bez určení viditelnosti | Repa jsou většinou veřejná, data jsou citlivá | viz níže |
| R4 | Notifikace přes Discord | Všechny ostatní souhrny chodí na Telegram (broker, audit) | Telegram (čeká na Olina) |
| R5 | Klientská repa „bez souhlasu“ | Allowlist | Klientská repa jsou výchozím stavem mimo allowlist; seznam jen v privátním configu |

## Rozhodnuto Olinem (2. 11. 2026)

- **R1:** profil `train` v brokeru; Opus píše zadání pro broker session, spouští Olin.
- **R2:** přepisy se čistí na M2, na SPARK jde jen vyčištěný `sft.jsonl`/`dpo.jsonl`.
- **R3:** repo `lioilsources/agenttune` je **veřejné**. Proto do gitu nikdy: přepisy ani jejich výňatky, `data/raw`, `data/out`, `data/REVIEW.md`, `rules.md` s příklady ze sessions, výsledky evalu s diffy, modely, jména klientských rep, LAN adresy a cesty. Kód, konfigurace, popisy úloh nad veřejnými repy a souhrnná čísla ano. Hlídá to pre-commit hook DevConventions a `.gitignore`.
- **Runner evalu v Go** (místo výchozího Pythonu); trénovací skripty zůstanou v Pythonu kvůli knihovnám (D5 v PLAN-infra).

## Rozhodnuto (Opus, podle §5 plánu)
- Inventarizační skripty běží na M2 a jen čtou; vypisují počty, žádný obsah sessions.

- Runner nezávisí na harnessu: agent je šablona příkazu. Volba Aider / OpenCode / Claude Code přes proxy (§5, ptát se Olina) tím neblokuje fázi 1.
- Úlohy klonují repo přes `git clone --shared` do dočasné složky; nevzniká worktree ve zdrojovém repu (DevConventions zakazuje editovat worktree).
- Holdout se dělí po repech (status-collector je v pilotu celý holdout), ne po commitech.
- `go-errors-wrap` hlásí jen holé `return err` po volání jiného balíčku nebo metody (`pkg.F()`, `x.M()`), jak říká plán.

## Zjištění, která mění plán

- **Data mizí.** Nejstarší přepis je z 25. 9. 2026 a `cleanupPeriodDays` není nastavené, takže Claude Code mazal přepisy starší než 30 dní. Bez změny nastavení přijdeme každý den o další. Viz `DATA_INVENTORY.md`.
- Dat je málo: 91 sessions a 2 518 lidských turnů. Na SFT trajektorií to stačí jen na malý dataset; preferenční páry z oprav budou stovky, ne tisíce.
- `go-errors-wrap` (z `shared/stacks/go.md`) porušují stávající commity ve všech Go repech, 382× na 161 commitech. Buď je pravidlo jen přání, nebo chybí vynucení. Rozhodnutí patří do DevConventions, ne sem.
