# AI in Design: start repo

A starting point for building a web app about a city with a coding agent. It holds a city kit of open data, the rules the agent reads first, a starter prompt, and a bare app that GitHub Pages publishes on every push.

## Make your own copy

In the folder where you keep projects:

```
gh repo create city-reach --template Bauhaus-InfAU/ai-in-design --public --clone
cd city-reach
code .
```

Then turn on publishing: on GitHub, open your repo, Settings, Pages, and set Source to "GitHub Actions". After your next push the site is at `https://<your-username>.github.io/city-reach/`.

## Run it

```
npm install     install the packages
npm run dev     start the app at http://localhost:5173
npm run build   build it the way GitHub Pages does
```

The start page says "Nothing here yet" and names the city kit. If it says the kit didn't load, something is wrong with the setup.

## What's in here

| Path | What it is |
| --- | --- |
| `CLAUDE.md` | the rules the agent reads every time it starts: the stack, hosting, how to treat the data |
| `prompt.md` | the starter prompt: a map of how far you can walk in the city, and who lives there |
| `public/data/` | the city kit; `SOURCES.md` there lists every file, its source, licence and fields |
| `src/` | the app; for now only a start page |
| `scripts/kit/` | the scripts that make the city kit: `weimar_lab.py` for Weimar, `build.py` for any other place |
| `private/` | for data that must never be published; git ignores it |
| `.github/workflows/` | builds the app and publishes it to GitHub Pages on every push to `main` |

## The city kit

Central Weimar, about 1.3 × 2 km: buildings with measured heights and their uses, plots with their land use, a pedestrian network you can route on, and the 2022 census on a 100 m grid. Read `public/data/SOURCES.md` before you use it.

To make a kit for another place, install [uv](https://docs.astral.sh/uv/) and run:

```
uv run scripts/kit/build.py --place "Erfurt" --size 2000
```

It replaces the files in `public/data/`. Its buildings and streets come from Overture Maps and work anywhere, with fewer fields than the Weimar kit. The census grid covers Germany only.

## Data and credit

Buildings: 3D building models LoD2 © GDI-Th (dl-de/by-2-0). Land use and pedestrian network: Egor Gaydukov, InfAU, Bauhaus-Universität Weimar. Census grid: Zensus 2022, © Statistische Ämter des Bundes und der Länder (dl-de/by-2-0). Any page that shows the data carries this line.
