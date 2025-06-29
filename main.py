import json
import os
import random
from collections import defaultdict
from functools import lru_cache
from typing import Annotated, Any, Generator, Iterable

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
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
    "lawrence-org-chart-2": DEFAULT_NORMALIZER
    + r".replace(/Department$/i, '').replace(/\s+and\s+/i, ' & ').trim()",
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


@lru_cache(maxsize=10)
def load_data(text_file_path: str) -> pd.DataFrame:
    df = pd.read_csv(text_file_path, sep="\t", keep_default_na=False)  # type: ignore
    # Add boolean ancestor columns when a hierarchical “parent” column is present
    # ------------------------------------------------------------------
    if {"parent", "name"}.issubset(df.columns):
        # 1. Create one column for every unique parent value (ignoring NaNs)
        unique_parents = df["parent"].dropna().unique().tolist()
        # Assert no parent value exists as a column name
        assert not any(p in df.columns for p in unique_parents), (
            "Parent value clashes with existing column"
        )
        # Add all ancestor columns at once using pd.concat
        new_cols = {p: pd.NA for p in unique_parents}
        df = pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)

        # 2. Build a quick lookup: child name -> immediate parent
        parent_map: dict[str, list[str]] = defaultdict(list)
        for row in df.itertuples():
            name = row.name
            if pd.notna(row.parent):
                parent_map[name].append(row.parent)

        # 3. Helper to get the full ancestor chain for a given item
        def enumerate_ancestors(name: str) -> Generator[tuple[int, str], None, None]:
            queue: list[tuple[int, list[str]]] = [(0, [name])]
            seen: set[str] = set()
            while queue:
                i, parents = queue.pop()
                for parent in parents:
                    if parent in seen:
                        continue
                    seen.add(parent)
                    yield i, parent
                    queue.append((i + 1, parent_map[parent]))

        # 4. Mark the ancestor columns for each row
        for idx, child_name in df["name"].items():
            for i, ancestor in enumerate_ancestors(child_name):
                df.loc[idx, ancestor] = i

    return df


@lru_cache(maxsize=10)
def get_data(
    text_file_path: str,
    level: int,
    parent: tuple[str] | None,
    exclude_parent: tuple[str] | None,
    **kwargs: Any,
) -> tuple[pd.DataFrame, list[str] | None]:
    df = load_data(text_file_path)
    if "parent" in df.columns:
        select_options = sorted(df["parent"].dropna().unique().tolist())  # type: ignore
    else:
        select_options = None
    if parent:
        df = pd.concat([df[(df[p] <= level) & (df["name"] != p)] for p in parent])
    if exclude_parent:
        for exc in exclude_parent:
            df = df[df[exc].isna()]

    # Apply any column‑specific filters passed as keyword arguments
    for k, v in kwargs.items():
        if k not in df.columns:  # type: ignore
            raise HTTPException(status_code=400, detail=f"Invalid parameter {k!r}")
        df = df[df[k] == v]  # type: ignore

    return df, select_options  # type: ignore


@app.get("/play/{text_file_name}")
async def return_memory_game(
    request: Request,
    text_file_name: str,
    lives: int = 5,
    sort: bool = False,
    level: int = 1,
    parent: Annotated[list[str] | None, Query()] = None,
    exclude_parent: Annotated[list[str] | None, Query()] = None,
) -> HTMLResponse:
    text_file_path = os.path.join(os.path.dirname(__file__), "data", text_file_name)
    is_tsv = os.path.exists(text_file_path + ".tsv")
    is_txt = os.path.exists(text_file_path + ".txt")
    entries: list[str]
    if is_tsv:
        text_file_path += ".tsv"
        df, select_options = get_data(
            text_file_path,
            level=level,
            parent=tuple(parent or []),
            exclude_parent=tuple(exclude_parent or []),
        )
        entries = df["name"].tolist()  # type: ignore

    elif is_txt:
        text_file_path += ".txt"
        with open(text_file_path) as f:
            entries = [line.strip() for line in f if line.strip()]
        select_options = None
    else:
        return HTMLResponse("File not found", status_code=404)
    if sort:
        entries = sorted(entries, key=lambda x: x.lower())
    game_title = _get_game_title_from_filename(text_file_name)
    if parent:
        # update game title to include parent values
        game_title += " (" + ", ".join(parent) + ")"
    return templates.TemplateResponse(
        "memory_game.html",
        {
            "request": request,
            "entries": entries,
            "game_title": game_title,
            "normalize_func": GAME_NORMALIZERS.get(text_file_name, DEFAULT_NORMALIZER),
            "lives": lives,
            "select_options": select_options,
        },
    )


@app.get("/play-table/{text_file_name}")
async def return_table_memory_game(
    request: Request,
    text_file_name: str,
    exclude: list[str] | None = None,
    level: int = 1,
) -> HTMLResponse:
    text_file_path = os.path.join(
        os.path.dirname(__file__),
        "data",
        text_file_name + ".tsv",
    )
    if not os.path.exists(text_file_path):
        return HTMLResponse("File not found", status_code=404)
    df, _ = get_data(
        text_file_path,
        level=level,
        parent=None,
        exclude_parent=None,
    )
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
