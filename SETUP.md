# Setting up Pac that Det(A)

This guide takes you from an empty machine to the app running in your browser. It needs no database and no build step: the backend serves the frontend files itself.

## What you need

- Python 3.10 or newer (developed and tested on 3.13). Check with `python3 --version` (on Windows, `py --version`).
- pip, which comes with Python.
- A modern browser. The tests use Chromium; other browsers have not been tested.
- About 500 MB of free memory for the largest case. A dense 10 x 10 matrix sorted by value temporarily uses about 400 MB on the server.

Docker is optional (see the last section).

## 1. Get the files

Unzip `determinant-app.zip` and open a terminal in the `determinant-app` folder. It should contain `main.py`, `requirements.txt`, and a `static` folder.

    cd determinant-app

## 2. Create a virtual environment

A virtual environment keeps the app's packages separate from the rest of your system.

macOS or Linux:

    python3 -m venv .venv
    source .venv/bin/activate

Windows (PowerShell):

    py -m venv .venv
    .venv\Scripts\Activate.ps1

If PowerShell refuses to run the script, run `Set-ExecutionPolicy -Scope Process RemoteSigned` once in that window and try again. When the environment is active, your prompt starts with `(.venv)`.

### Using Anaconda or Miniconda instead

Skip the venv commands above and use a conda environment. In Anaconda Prompt (Windows) or any terminal with conda:

    conda create -n pacdet python=3.12
    conda activate pacdet

Then continue with step 3. Install with `pip` inside the activated environment, as shown, so FastAPI and Uvicorn come from PyPI (the default conda channels can lag behind the versions the app needs). If you prefer conda, `conda install -c conda-forge fastapi uvicorn pydantic` also works. If you use the base environment instead, check that `python --version` is 3.10 or newer.

## 3. Install the dependencies

    pip install -r requirements.txt

This installs FastAPI, Uvicorn and Pydantic. jQuery is already included in `static/jquery.min.js`, so nothing is fetched from the internet when the page loads.

## 4. Start the server

    uvicorn main:app --port 8000

Leave it running. You should see a line saying Uvicorn is running on http://127.0.0.1:8000.

Use exactly one worker (the default). Results are kept in the memory of one process, so several workers would not see each other's calculations.

## 5. Open the app

Go to http://localhost:8000 in your browser. A 6 x 6 example is already calculated, with determinant 1,986.

Quick check that everything works:

1. Change a cell and press Calculate determinant (or Ctrl or Cmd + Enter). The result banner updates.
2. In the Permutation steps card, try Show and Order, then hover a row to see the matrix popup.
3. Press Open the solution page to see the working in four steps.

To stop the server, press Ctrl + C in the terminal.

## Optional settings

- Another port: `uvicorn main:app --port 9000`.
- Reachable from other devices on your network: `uvicorn main:app --host 0.0.0.0 --port 8000`, then open `http://<your-computer-address>:8000` on the other device. There are no user accounts, so only do this on a network you trust.
- Behind a reverse proxy: add `--proxy-headers --forwarded-allow-ips <proxy address>` so the rate limiter sees each visitor's address instead of the proxy's.
- Auto-reload while editing code: `uvicorn main:app --reload --port 8000` (development only).

## Running the tests

    pip install -r requirements-dev.txt
    playwright install chromium
    pytest

There are 114 tests and they take about 20 seconds. The browser tests start their own server on a free port, so you do not need to start one first. If Playwright or its browser is missing, the browser tests skip themselves and the API tests still run.

## Running with Docker

    docker build -t pac-that-det-a .
    docker run -p 8000:8000 pac-that-det-a

Then open http://localhost:8000. The image uses Python 3.12 and one Uvicorn worker. The Dockerfile has not been built in the environment where the app was developed, so build it once before relying on it.

## Troubleshooting

| Problem | Fix |
|---|---|
| `python3: command not found` | Install Python from python.org, or use `py` on Windows. |
| `No module named uvicorn` or `fastapi` | The virtual environment is not active or the install step was skipped. Activate it and run `pip install -r requirements.txt` again. |
| `Address already in use` | Another program uses port 8000. Pick another with `--port 8001`. |
| Page loads but is blank or unstyled | Run the command from the folder that contains `main.py` and `static`, not from inside `static`. |
| "calculation not found or expired" | Only the 8 most recent calculations are kept for an hour. The page resubmits your matrix by itself; press Calculate if it does not. |
| First sorted view of a 10 x 10 takes several seconds | Expected: the server builds a sorted index once per calculation. After that, paging is instant. |
| Tests skip or Chromium fails to start | Run `playwright install chromium`. On Linux you may also need `playwright install-deps chromium`. |

For how the app works inside, every control, and the API, see `README.md`.
