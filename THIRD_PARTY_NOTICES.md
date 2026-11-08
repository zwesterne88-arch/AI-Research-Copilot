# Third-party notices

The MIT license at the repository root applies to this project's original code.
Third-party assets retain their original licenses and copyright notices.

## Bundled browser assets

The `web/vendor/` directory contains Mozilla **PDF.js / pdfjs-dist 6.4.299**.
Source package and original SHA-256 are recorded in `web/vendor/manifest.json`.
The project distributes the package's browser modules, viewer stylesheet, character maps,
standard fonts and WebAssembly assets without replacing their license notices.

- PDF.js: [Apache License 2.0](web/vendor/LICENSE), upstream <https://github.com/mozilla/pdf.js>.
- Character maps: [original notice](web/vendor/cmaps/LICENSE).
- Foxit fonts: [original notice](web/vendor/standard_fonts/LICENSE_FOXIT).
- Liberation fonts: [original notice](web/vendor/standard_fonts/LICENSE_LIBERATION).
- WebAssembly components: `LICENSE_JBIG2`, `LICENSE_OPENJPEG`, `LICENSE_QCMS`,
  `LICENSE_PDFJS_JBIG2`, `LICENSE_PDFJS_OPENJPEG` and `LICENSE_PDFJS_QCMS` in [web/vendor/wasm](web/vendor/wasm).

These notices must accompany redistribution of the corresponding assets.

## Installed dependencies

Python packages are installed separately using `requirements.txt`; their code is not copied
from the developer's virtual environment into this repository. Their own licenses apply.
Optional Argos translation models and runtime packages are downloaded separately and are
not included in the source distribution. Review the model package's original license when
installing or redistributing it.

The repository does not distribute research-paper PDFs, user databases, provider API keys,
or historical model-response datasets. API provider names identify supported integrations;
they do not imply endorsement or bundled API access.
