# Project guidelines

You are helping a student of architecture, urban design or a related field build a web app about a city. Most have never written code. They decide what the app is for and check what you build. You write the code.

## How to work with the student

- Explain each step in plain words, as you go, in a sentence or two. No jargon without a one-line explanation.
- Before you run a command, say what it does and why.
- After a change, say which files you changed, and what the student should look at on the page to check it.
- One task at a time. If a request is vague, ask one question rather than guessing.
- If something fails, show the error and say what it means before you fix it.
- Don't commit or push unless the student asks. Never force-push, and never rewrite history, unless the student asks for exactly that.

## Stack

Keep to it. Don't switch frameworks or add TypeScript.

- **Vite** for the build and the dev server
- **React**, in **JavaScript**
- **shadcn/ui** for UI components, **react-icons** for icons
- **MapLibre GL JS** for 2D maps, only if needed
- **Three.js / React Three Fiber** for 3D, only if needed
- **npm** as the package manager

Small libraries for a clear purpose are fine, such as `@turf/turf` for geometry. Say which one you add and why.

No backend. Calls to paid services, such as an image API, run from a script on the student's laptop, in `scripts/`. The key lives in `.env`, which `.gitignore` excludes; it never goes into the page or the repo. The script saves its result into the app's data, and the result is what gets pushed.

## Hosting

- The app is published by GitHub Pages through the workflow in `.github/workflows/`, on every push to `main`. Don't change the workflow unless the student asks.
- The site lives at `https://<user>.github.io/<repo>/`, not at the root. In `vite.config.js`, the build's `base` comes from the repo name in the `GITHUB_REPOSITORY` variable that Actions sets; locally it is `/`.
- Load every data file through `import.meta.env.BASE_URL`, for example `` fetch(`${import.meta.env.BASE_URL}data/buildings.geojson`) ``. A path that starts with `/data/` works on localhost and breaks on Pages.
- Commit `package-lock.json`. The build runs `npm ci`, which needs it.

## Data

The city kit is in `public/data/`. A generator in `scripts/kit/` made it from open data: buildings and streets from Overture Maps, which builds on OpenStreetMap, and the census grid from Zensus 2022. Don't edit these files by hand. To rebuild the kit for another area, the student runs `uv run scripts/kit/build.py --place "<place>" --size 2000`; the default example is `--place "Weimar"`. The census grid only covers Germany.

`SOURCES.md` lists every file with its source, date, licence and fields. Read it before you use any data. Check the exact field names in the files themselves.

All coordinates are WGS84 longitude and latitude, longitude first, as GeoJSON requires. For 3D or for distances, project to local metres around the kit's centre, `center` in `kit.json`. Don't hard-code a city's coordinates.

| File | What it is | Units and traps |
| --- | --- | --- |
| `kit.json` | the kit area: `center`, `bbox`, `size_m` | `center` is `[lon, lat]`, `bbox` is `[west, south, east, north]` |
| `buildings.geojson` | building footprints | `height` in metres, missing on about half the buildings; `height_source` says where each height comes from: `osm` (mapped), `floors` (floors × 3 m), `ml` (estimated from aerial images), or `null` |
| `streets.geojson` | street centre lines, for drawing | a line drawing, not a network |
| `streets.graph.json` | the street network: `nodes` with `id`, `lon`, `lat`; `edges` with `from`, `to`, `length`, `walkable`, `coords` | edge `length` in metres along the street; edges run both ways; `coords` is the line from `from` to `to`. Distances along streets come from here, never from straight lines |
| `zensus.geojson` | census grid, 100 m cells touching the kit | `population` in persons, `avg_age` in years, `rent_eur_m2` in € per m² per month, `vacancy_pct` in percent, `heating_main`. A cell is an area, not a building. `null` means no published figure, which isn't zero. `uncertain` lists values the census marks as unreliable |

Other data may be added later. Each new file gets its entry in `SOURCES.md`.

## Never invent data

- If a value is missing, show it as missing or use a default that you state on the page and in the README, and mark it visually. Building heights without a value get a stated default height and a lighter tone. Never fill them with a plausible guess.
- Census cells without a published figure (`null`) are left out of sums, and the page says how many were left out. Never count them as zero.
- Don't make up numbers, names, dates or facts about the city from what you think you know. Use the kit, or a source the student gives you, and name it.
- Every method that changes a number goes on the page in plain words: walking speed, buffer widths, how cells are shared out, defaults.

## `private/`

`private/` is in `.gitignore`. It holds data that must never be published: interviews, personal photos, anything with names or addresses.

- Never copy anything from `private/` into `public/`, `src/` or any other tracked file.
- Never load it in the app, and never put its content into the README or a commit message.
- Only read it when the student asks, for a script that runs on their laptop. Tell the student what you read.
- Never remove `private/` from `.gitignore`.

## Credit

The app shows one attribution line on the map, naming every data source it uses with its licence, worded as in `SOURCES.md`. When the student adds a dataset, add it to `SOURCES.md` and to the attribution line.

## README

`README.md` says what the app does, how to run it, the method and its limits, and the data sources. It ends with a section "How this was made" with four lines:

1. Tools: the coding agent, and the models the app calls.
2. I decided: the question, the references, the main choices, what I rejected.
3. The agent did: wrote the code, processed the data, drafted the style guide.
4. I checked: what was tested and how, and the known limits.

Fill in lines 1 and 3 and keep them up to date. Lines 2 and 4 belong to the student. Never write or change them; leave them empty if they are empty.

## Commands

```
npm install     install the packages
npm run dev     start the app at http://localhost:5173
npm run build   build it the way GitHub Pages does
```
