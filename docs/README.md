# `/docs` — public site for lgthinktank.com

This directory is the source for the **lgthinktank.com** public site, served by
GitHub Pages from `main` / `/docs`.

## Layout

```
docs/
├── CNAME                         # lgthinktank.com (custom domain)
├── .nojekyll                     # disable Jekyll processing
├── 404.html
├── favicon.svg
├── robots.txt
├── sitemap.xml
├── index.html                    # apex think-tank landing
├── assets/
│   ├── theme.css                 # shared light/dark theme
│   └── theme.js                  # theme toggle (light is default)
└── los-gatos-transit/            # first project
    ├── index.html                # project landing (persuasive hook)
    ├── analysis.html             # full CBA dashboard (built from outputs/)
    ├── route-redesign.html       # coming-soon page
    └── stops/                    # 140 stop placards
```

## Rebuilding after the pipeline runs

The dashboard and stop placards are generated from the Python pipeline output
in `outputs/`. To refresh the public site:

```bash
python scripts/build_site.py
```

This copies `outputs/cba_dashboard.html` and `outputs/placards/*.html` into
`docs/los-gatos-transit/`, applying the public-facing hero, theme overrides,
plain-English subtitle rewrites, and link corrections. Hand-authored files
(`docs/index.html`, `docs/los-gatos-transit/index.html`, `route-redesign.html`,
landing/coming-soon copy) are not touched by the build script — edit them
directly.

## One-time GitHub Pages setup

1. **Repo Settings → Pages**
   - Source: **Deploy from a branch**
   - Branch: **`main`** · Folder: **`/docs`**
2. **Custom domain:** `lgthinktank.com` (the `CNAME` file in this directory
   is what GitHub reads).
3. **DNS at your registrar** — set these records on `lgthinktank.com`:
   - `A`     `@`   `185.199.108.153`
   - `A`     `@`   `185.199.109.153`
   - `A`     `@`   `185.199.110.153`
   - `A`     `@`   `185.199.111.153`
   - `CNAME` `www` `<your-github-username>.github.io.`
4. After DNS propagates (10 min – a few hours), tick **Enforce HTTPS** in
   the Pages settings.

## Theming

Light mode is the default. Dark mode activates when the visitor either:
- clicks the toggle in the header (choice persists in `localStorage`), or
- has `prefers-color-scheme: dark` set and hasn't clicked the toggle yet.

Both modes use the same CSS variable names, so any future page that loads
`/assets/theme.css` automatically gets both themes for free.
