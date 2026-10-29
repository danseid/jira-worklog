# Jira worklog

A local web page that shows your Jira worklogs for one month as a grid: one row per ticket,
one column per day. Click a cell to add a worklog for that ticket and day.

## Start

You need [mise](https://mise.jdx.dev/). It installs Python and [uv](https://docs.astral.sh/uv/)
in the versions that `mise.toml` pins. Then run:

```bash
git clone https://github.com/danseid/jira-worklog
cd jira-worklog
mise install
mise run start
```

`mise run start` installs the dependencies from `uv.lock` on first use and starts the app.

On the first start the app asks for two values in the terminal:

1. The Jira URL, for example `https://jira.example.com`.
2. A Jira personal access token. Make one in Jira under
   **Profile → Personal Access Tokens**. The input is hidden.

The app tests the values with Jira and saves them in `~/.config/jira-worklog/config.env`.
Only you can read this file (mode 600). Then the app opens `http://127.0.0.1:8765/` in your
browser. Stop it with Ctrl+C.

Without mise, the same app runs straight from the repository with
[uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uvx --from git+https://github.com/danseid/jira-worklog jira-worklog
```

## Options

| Option | What it does |
|---|---|
| `--jira-url URL` | Use this Jira URL |
| `--setup` | Ask for the URL and the token again, for example after the token expires |

The token is never a command-line option, because the shell history and the process list
would show it.

## Settings without the prompt

The app reads the settings in this order. The first value found wins.

1. Environment variables
2. A `.env` file in the current folder (see `.env.example`)
3. `~/.config/jira-worklog/config.env`

| Variable | Meaning |
|---|---|
| `JIRA_URL` | Jira base URL |
| `JIRA_PERSONAL_TOKEN` | Personal access token (Bearer) |
| `JIRA_USERNAME` + `JIRA_API_TOKEN` | Basic authentication, instead of the token |
| `JIRA_VERIFY_SSL` | `false` turns off the certificate check. The default is `true` |

## What the app does

- It shows the worklogs that you wrote. It never shows the worklogs of other users.
- A save always adds a new worklog. The app never changes or deletes a worklog.
- A new worklog starts at 09:00 Europe/Berlin plus the time already logged on that day.
- The target is 8 h per workday, Monday to Friday. The app does not know public holidays or leave.
- The app listens on `127.0.0.1` only. The token stays in the app and never goes to the browser.
- "Export CSV" downloads the worklogs of the month.

## Limits

- Jira Data Center and Jira Server only. Jira Cloud does not work yet: the app uses the user
  field `name`, which Jira Cloud does not have.
- The app uses the time zone Europe/Berlin.
- Port 8765 is fixed.

## Development

Everything is pinned in `mise.toml`: mise provides Python and uv, uv owns the dependencies in
`uv.lock`.

| Task | What it does |
|---|---|
| `mise run start` | Start the dashboard |
| `mise run install` | Install the dependencies from `uv.lock` |
| `mise run test` | Run the tests |
| `mise run setup` | Ask for the URL and the token again |
| `mise run upgrade` | Upgrade the dependencies within `pyproject.toml` |

## License

MIT. The page uses [htmx](https://htmx.org/) (0BSD license), included in `src/jira_worklog/static/`.
