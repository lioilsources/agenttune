# Eval

Malý SWE-bench z vlastních rep. Úloha = repo na commitu před opravou, prompt, oracle testy z opravného commitu a seznam pravidel, která se kontrolují na diffu agenta.

## Postup

```sh
go build -o bin/evalctl ./cmd/evalctl
bin/evalctl candidates -n 40 ../PromoClown            # commity, které mění kód i test
bin/evalctl draft -repo lioilsources/PromoClown -dir ../PromoClown -sha a26389cf4 -id promoclown-final-failure-001
#   → evals/tasks/<id>/task.yaml; prompt přepsat ručně: problém a API, které test volá, bez řešení
bin/evalctl validate                                    # schéma, známá pravidla, žádné TODO
bin/evalctl verify -repos-root ..                       # oracle musí před opravou selhat a s opravou projít
bin/evalctl run -model <m> -repos-root .. -split dev \
  -agent-cmd '<harness> --model {model} --message-file {prompt_file}'
```

`run` klonuje repo přes `git clone --shared` do dočasné složky, takže zdrojová repa zůstanou netknutá. Agent je šablona příkazu (`{workdir}`, `{prompt_file}`, `{model}`, `{metrics_file}`), takže výběr harnessu (Aider, OpenCode, Claude Code přes LiteLLM) je jen konfigurace. Agent může do `{metrics_file}` zapsat `{"turns":N,"tokens":N}`. Výsledky (`evals/results/`) obsahují diffy, a proto nejsou v gitu.

## Go moduly a Flutter balíčky v podsložce

`draft` najde v commitu opravy nejbližší `go.mod`, případně `pubspec.yaml`, který obsahuje všechny testové soubory. Z jeho složky udělá `dir:` a cesty v `test_cmd` budou relativní k ní, například `dir: app` a `flutter test test/x_test.dart`. Když testy leží ve dvou modulech, `draft` skončí chybou a úlohu je potřeba rozdělit. Jednu úlohu ověří `verify -only <id>`.

Flutter úloha trvá na M2 minuty, ne sekundy. Většinu času zabere `pub get` nad pomalým pub cache a start `flutter test`, výpočet sám je krátký. Proto má sada víc úloh v Go a Flutter úlohy jsou hlavně čistá logika v `test/`, ne golden, integration_test ani screenshot testy.

## Pravidla

`bin/evalctl rules list` vypíše pravidla a jejich původ (modul DevConventions nebo plán). Každé pravidlo je funkce nad diffem v `internal/rules`, s testy na pozitivní i negativní případ.

## Kalibrace na skutečných commitech (2. 11. 2026)

`scripts/calibrate_rules.sh` pustí všechna pravidla na commity s kódem i testem v šesti repech (161 commitů). Po úpravách falešných nálezů zbývá:

| pravidlo | nálezy | závěr |
|---|---:|---|
| go-errors-wrap | 382 | pravidlo z `stacks/go.md` se v praxi porušuje všude; buď se vynutí (linter v CI), nebo se z modulu zmírní |
| no-new-deps | 36 | skutečné nové závislosti; úlohy, které je potřebují, mají `allow_deps: true` |
| flutter-no-hardcoded-text | 18 | jen appky s `l10n.yaml` (Kiran, SwypeKids) |
| no-fmt-println | 12 | Ol1nLLM |
| go-test-no-time-now | 8 | |
| tests-added | 6 | |
| flutter-no-logic-in-build | 1 | |

Opravené falešné nálezy: holé `return err` po volání ze stejného balíčku, vygenerované soubory (sqlc) v řádkových pravidlech, regenerace kódu spolu se změnou `.sql`, Text() v appkách bez l10n, klíče `flutter:`/`assets:` v pubspec, `await` v callbacku uvnitř build().
