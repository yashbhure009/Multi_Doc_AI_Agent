import builtins
import re
import numpy as np
import pandas as pd
_ALLOWED = (
    "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int",
    "isinstance", "len", "list", "map", "max", "min", "range", "reversed",
    "round", "set", "sorted", "str", "sum", "tuple", "zip",
)
SAFE_BUILTINS = {n: getattr(builtins, n) for n in _ALLOWED}
BLOCKED_PATTERNS = [
    r"__", r"\bimport\b", r"\bopen\s*\(", r"\beval\s*\(", r"\bexec\s*\(",
    r"\bcompile\s*\(", r"\bgetattr\s*\(", r"\bsetattr\s*\(", r"\bdelattr\s*\(",
    r"\bglobals\s*\(", r"\blocals\s*\(", r"\bvars\s*\(", r"\binput\s*\(",
    r"\bbreakpoint\s*\(", r"\bwhile\b", r"\bos\.", r"\bsys\.", r"\bsubprocess\b",
    r"\bshutil\b", r"\.eval\s*\(", r"\.read_\w+\s*\(", r"\bnp\.(load|save)\w*\s*\(",
    r"\.to_(csv|excel|pickle|json|sql|parquet|feather|hdf|clipboard|html|markdown)\s*\(",
]
def clean_code(text: str) -> str:
    code = text.strip()
    m = re.search(r"```(?:python)?\s*(.*?)```", code, re.S)
    if m:
        code = m.group(1)
    kept = [
        line for line in code.splitlines()
        if not re.fullmatch(r"\s*import (pandas as pd|numpy as np)\s*", line)
    ]
    return "\n".join(kept).strip()
def validate_code(code: str):
    if not code:
        return "Empty code."
    for p in BLOCKED_PATTERNS:
        if re.search(p, code):
            return f"Blocked unsafe code (pattern: {p})"
    return None
def run_code(code: str, tables: dict):
    dfs = {k: v.copy() for k, v in tables.items()}
    env = {"__builtins__": SAFE_BUILTINS, "pd": pd, "np": np, "dfs": dfs}
    if len(dfs) == 1:
        env["df"] = next(iter(dfs.values()))
    exec(compile(code, "<generated>", "exec"), env)  # single namespace
    return env.get("result")
def to_frame(result):
    if isinstance(result, pd.DataFrame):
        return result
    if isinstance(result, pd.Series):
        return result.to_frame(name=str(result.name or "value")).reset_index()
    return None
def result_text(result, max_rows: int = 40) -> str:
    frame = to_frame(result)
    if frame is None:
        return str(result)
    text = frame.head(max_rows).to_string(index=False)
    if len(frame) > max_rows:
        text += f"\n(showing first {max_rows} of {len(frame)} rows)"
    return text