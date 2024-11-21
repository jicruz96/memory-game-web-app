import os
import random
from typing import Iterable

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()
# Define the templates directory
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# define the javacript normalizer function for each game
DEFAULT_NORMALIZER = (
    r"(text) => text.toLowerCase().replace(/\s+/g, ' ').replace(/\(.*\)$/, '').trim()"
)
GAME_NORMALIZERS = {
    "massachusetts-counties": DEFAULT_NORMALIZER + r".replace(/county$/i, '').trim()",
}


def _get_game_title_from_filename(filename: str) -> str:
    return filename.removesuffix(".txt").removesuffix(".tsv").replace("-", " ").title()


def _get_game_files(remove_extension: bool = False) -> Iterable[str]:
    for filepath in os.listdir(os.path.join(os.path.dirname(__file__), "data")):
        filename = os.path.basename(filepath)
        if not filename.endswith(".txt") and not filename.endswith(".tsv"):
            raise ValueError(f"File {filename} does not end with a valid extension")
        if remove_extension:
            filename = filename.removesuffix(".txt").removesuffix(".tsv")
        yield filename


@app.get("/")
async def show_available_games() -> HTMLResponse:
    li: list[str] = [
        f'<li><a href="/play/{filename}">{_get_game_title_from_filename(filename)}</a></li>'
        for filename in _get_game_files(remove_extension=True)
    ]
    return HTMLResponse(f"<h1>Available Games</h1><ul>{'\n'.join(li)}</ul>")


@app.get("/play/{text_file_name}")
async def return_memory_game(request: Request, text_file_name: str) -> HTMLResponse:
    # file should be in data folder
    text_file_path = os.path.join(os.path.dirname(__file__), "data", text_file_name)
    is_tsv = os.path.exists(text_file_path + ".tsv")
    is_txt = os.path.exists(text_file_path + ".txt")
    entries: list[str]
    if is_tsv:
        text_file_path += ".tsv"
        df = pd.read_csv(text_file_path, sep="\t")  # type: ignore
        for k, v in request.query_params.items():
            if k == "lives":
                continue
            if k not in df.columns:
                raise HTTPException(status_code=400, detail=f"Invalid parameter {k!r}")
            df = df[df[k] == v]
        entries = df["name"].tolist()  # type: ignore
    elif is_txt:
        text_file_path += ".txt"
        with open(text_file_path) as f:
            entries = [line.strip() for line in f if line.strip()]
    else:
        return HTMLResponse("File not found", status_code=404)
    return templates.TemplateResponse(
        "memory_game.html",
        {
            "request": request,
            "entries": entries,
            "game_title": _get_game_title_from_filename(text_file_name),
            "normalize_func": GAME_NORMALIZERS.get(text_file_name, DEFAULT_NORMALIZER),
            "lives": request.query_params.get("lives", 5),
        },
    )


@app.get("/random")
async def go_to_random_game() -> RedirectResponse:
    return RedirectResponse(
        url=f"/play/{random.choice(list(_get_game_files(remove_extension=True)))}"
    )
