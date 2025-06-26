import json
import os
import random
from typing import Any, Iterable

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
DEFAULT_NORMALIZER = r"(text) => console.log(text) || text.toLowerCase().replace(/\s+/g, ' ').replace(/\(.*\)$/, '').trim()"
COUNTY_NORMALIZER = DEFAULT_NORMALIZER + r".replace(/county$/i, '').trim()"
GAME_NORMALIZERS = {
    "massachusetts-counties": COUNTY_NORMALIZER,
    "lawrence-org-chart": DEFAULT_NORMALIZER + r".replace(/Department$/i, '').trim()",
}
TABLE_GAME_NORMALIZERS = {
    "massachusetts-counties": {
        "name": "county_normalizer",
        "pop est.": "round_to_two_sigfigs_normalizer",
    }
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
        f'<li><a href="/play/{"".join(filename.split(".")[:-1])}">{_get_game_title_from_filename(filename)}</a></li>'
        for filename in _get_game_files()
    ]
    return HTMLResponse(f"<h1>Available Games</h1><ul>{'\n'.join(li)}</ul>")


def get_data(text_file_path: str, **kwargs: Any) -> pd.DataFrame:
    df = pd.read_csv(text_file_path, sep="\t")  # type: ignore

    # Apply any column‑specific filters passed as keyword arguments
    for k, v in kwargs.items():
        if k == "parent":
            continue
        if k not in df.columns:  # type: ignore
            raise HTTPException(status_code=400, detail=f"Invalid parameter {k!r}")
        df = df[df[k] == v]  # type: ignore

    # ------------------------------------------------------------------
    # Add boolean ancestor columns when a hierarchical “parent” column is present
    # ------------------------------------------------------------------
    if {"parent", "name"}.issubset(df.columns):
        # 1. Create one column for every unique parent value (ignoring NaNs)
        unique_parents = df["parent"].dropna().unique().tolist()
        for p in unique_parents:
            # Avoid clashing with existing columns (e.g., numeric data fields)
            if p not in df.columns:
                df[p] = 1_000

        # 2. Build a quick lookup: child name -> immediate parent
        parent_map = dict(zip(df["name"], df["parent"]))

        # 3. Helper to get the full ancestor chain for a given item
        def _ancestors(name: str) -> list[str]:
            chain: list[str] = []
            while pd.notna(parent_map.get(name)):  # walk up until no parent
                name = parent_map[name]
                chain.append(name)
            return chain

        # 4. Mark the ancestor columns for each row
        for idx, child_name in df["name"].items():
            for i, ancestor in enumerate(_ancestors(child_name)):
                df.at[idx, ancestor] = i

    if "parent" in kwargs:
        parent = kwargs["parent"]
        df = df[df[parent] <= kwargs.get("level", 0)]
    return df  # type: ignore


@app.get("/play/{text_file_name}")
async def return_memory_game(request: Request, text_file_name: str) -> HTMLResponse:
    # file should be in data folder
    text_file_path = os.path.join(os.path.dirname(__file__), "data", text_file_name)
    is_tsv = os.path.exists(text_file_path + ".tsv")
    is_txt = os.path.exists(text_file_path + ".txt")
    entries: list[str]
    if is_tsv:
        text_file_path += ".tsv"
        filter_query_params = {
            k: v for k, v in request.query_params.items() if k != "lives"
        }
        entries = get_data(text_file_path, **filter_query_params)["name"].tolist()  # type: ignore
    elif is_txt:
        text_file_path += ".txt"
        with open(text_file_path) as f:
            entries = [line.strip() for line in f if line.strip()]
    else:
        return HTMLResponse("File not found", status_code=404)
    print(entries)
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


@app.get("/play-table/{text_file_name}")
async def return_table_memory_game(
    request: Request,
    text_file_name: str,
    exclude: list[str] | None = None,
) -> HTMLResponse:
    text_file_path = os.path.join(
        os.path.dirname(__file__),
        "data",
        text_file_name + ".tsv",
    )
    if not os.path.exists(text_file_path):
        return HTMLResponse("File not found", status_code=404)
    filter_query_params = {
        k: v for k, v in request.query_params.items() if k != "exclude"
    }
    df = get_data(text_file_path, **filter_query_params)
    if exclude:
        if "name" in exclude:
            raise HTTPException(status_code=400, detail="Cannot exclude name")
        df = df.drop(exclude, axis=1)
    # convert dataframe to a 2d list
    return templates.TemplateResponse(
        "table_memory_game.html",
        {
            "request": request,
            "table_headers": df.columns.tolist(),
            "table_data": [
                [
                    {
                        "answer": cell,
                        "submissionNormalizer": TABLE_GAME_NORMALIZERS.get(
                            text_file_name, {}
                        ).get(
                            header,
                            "stringNormalizer"
                            if isinstance(cell, str)
                            else "numberNormalizer",
                        ),
                    }
                    for header, cell in zip(df.columns, row)
                ]
                for row in df.values  # type: ignore
            ],
            "show_headers": True,
        },
    )


@app.get("/random")
async def go_to_random_game() -> RedirectResponse:
    return RedirectResponse(
        url=f"/play/{random.choice(list(_get_game_files(remove_extension=True)))}"
    )
