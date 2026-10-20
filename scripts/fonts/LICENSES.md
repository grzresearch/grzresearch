# Embedded fonts

The SVGs embed these subsets as base64 WOFF2, so every visitor sees the same
typography even though GitHub renders the images without network access.
All are licensed under the [SIL Open Font License 1.1](https://openfontlicense.org);
the copyright and license records are kept inside each file.

| File | Source | Copyright |
| --- | --- | --- |
| `jbm-400.woff2`, `jbm-700.woff2` | JetBrains Mono 2.304 | © 2020 The JetBrains Mono Project Authors |
| `inter-300.woff2` | Inter Display 4.0 Light | © 2016 The Inter Project Authors |
| `katex-math.woff2`, `katex-main.woff2` | KaTeX 0.16.9 Math-Italic / Main-Regular | © 2009–2010 Design Science, Inc.; © 2014–2018 Khan Academy |

The KaTeX fonts carry the Reserved Font Names *KaTeX_Math* and *KaTeX_Main*,
so these modified subsets are declared under other family names (`KTM`, `KTR`).

Subsets were cut with [fontTools](https://github.com/fonttools/fonttools):

```sh
pyftsubset JetBrainsMono-Regular.ttf --text="…" --flavor=woff2 --layout-features= \
  --no-hinting --desubroutinize --name-IDs=0,13,14 --output-file=jbm-400.woff2
```
