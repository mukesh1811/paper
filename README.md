# Paper

Paper is a focused reading layer for public documents: paste a public PDF URL and get a reflowed, Kindle-style reading experience. PDFs are the first supported source; the broader direction is a better way to read anything from the public internet.

The repository is a monorepo: `site/` contains the GitHub Pages frontend source, while `api/` contains the FastAPI/PyMuPDF service deployed to Cloud Run.

The app includes a public marketing site, keyword-focused guides, an editorial journal, and the reader itself. The product stays intentionally narrow: it helps people read; it does not add accounts, libraries, chat, summaries, annotations, or dashboards.

## MVP principles

- URL in → book view out
- no account, library, chat, annotations, or upload step
- serif typography, theme/font/width/spacing controls
- reading progress saved in the browser
- server fetches the PDF to avoid browser CORS problems
- repeated headers/footers and page numbers are heuristically removed
- scanned/image-only PDFs are intentionally unsupported in v0

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r api/requirements.txt
python -m uvicorn api.app:app --reload
```

Open `http://localhost:8000`.

The homepage keeps a prefilled Tolstoy form for an immediate product demo. The focused reader entry page is `http://localhost:8000/read`.

Useful public pages:

- `http://localhost:8000/features`
- `http://localhost:8000/how-it-works`
- `http://localhost:8000/read`
- `http://localhost:8000/read-pdf-online`
- `http://localhost:8000/read-pdf-on-phone`
- `http://localhost:8000/read-pdf-like-a-book`
- `http://localhost:8000/pdf-reflow`
- `http://localhost:8000/read-pdf-without-downloading`
- `http://localhost:8000/open-pdf-link-online`
- `http://localhost:8000/pdf-reader-for-long-documents`
- `http://localhost:8000/read-research-papers`
- `http://localhost:8000/formats`
- `http://localhost:8000/blog`
- `http://localhost:8000/robots.txt`
- `http://localhost:8000/sitemap.xml`

Direct document links work too:

```text
http://localhost:8000/read?url=https%3A%2F%2Fexample.com%2Fbook.pdf
```

## Deploy

The included Dockerfile works on any container host (Cloud Run, Railway, Render, Fly.io, etc.). Set the platform's `PORT` normally; the container defaults to 8080.

Set `PAPER_SITE_URL` to the canonical public origin in production so the generated sitemap points to the deployed site, for example `https://paper.example.com`. For the GitHub Pages project site, that origin will be `https://mukesh1811.github.io/paper` once the static Pages build is wired.

## Security / limits

The fetcher rejects non-http(s) URLs, credentials in URLs, private/local IP targets, and revalidates redirect targets. PDFs are capped at 30 MB. This is still an MVP; production hardening should add egress policy, rate limiting, cache controls, abuse protection, and stricter DNS-rebinding defenses.

## Next useful iteration

1. better paragraph reconstruction using coordinates/indentation
2. preserve italics and scene breaks
3. chapter navigation
4. cache extracted books by URL + ETag
5. optional OCR fallback for scanned PDFs
6. installable PWA / offline reading
## Deployment

The static frontend is published from `site/` through GitHub Pages. Set the repository variable `PAPER_API_URL` to the public Cloud Run URL before enabling the Pages workflow. Set `PAPER_SITE_URL` and `PAPER_ALLOWED_ORIGINS` on the Cloud Run service; the latter should include `https://mukesh1811.github.io/paper` and any local development origin you use.
