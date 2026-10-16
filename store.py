import hashlib
import io
import uuid
from dataclasses import dataclass
import chromadb
import pandas as pd
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
TABLE_EXT = (".csv", ".xlsx", ".xlsm", ".xls")
_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
@dataclass
class PdfInfo:
    name: str
    pages: int
    chunks: int
    empty_pages: int
    preview: str
@dataclass
class TableInfo:
    key: str
    file: str
    sheet: str
    df: pd.DataFrame
class FileStore:
    def __init__(self):
        self._client = chromadb.EphemeralClient()
        self._collection = self._client.get_or_create_collection(
            name=f"uploads_{uuid.uuid4().hex}",
            metadata={"hnsw:space": "cosine"},
        )
        self.pdfs: dict[str, PdfInfo] = {}
        self.tables: dict[str, TableInfo] = {}
        self._digests: dict[str, str] = {}
        self._failed: dict[tuple, str] = {}
    def add_file(self, name: str, data: bytes) -> None:
        digest = hashlib.sha256(data).hexdigest()
        if (name, digest) in self._failed:
            raise ValueError(self._failed[(name, digest)])
        if self._digests.get(name) == digest:
            return
        if name in self._digests:
            self.remove_file(name)
        lower = name.lower()
        try:
            if lower.endswith(".pdf"):
                self._add_pdf(name, data)
            elif lower.endswith(TABLE_EXT):
                self._add_tables(name, data)
            else:
                raise ValueError("Unsupported file type.")
        except Exception as e:
            self._purge(name)
            msg = f"{type(e).__name__}: {e}"
            self._failed[(name, digest)] = msg
            raise ValueError(msg)
        self._digests[name] = digest
    def remove_file(self, name: str) -> None:
        self._purge(name)
        self._digests.pop(name, None)
    def _purge(self, name: str) -> None:
        try:
            self._collection.delete(where={"file": name})
        except Exception:
            pass
        self.pdfs.pop(name, None)
        for k in [k for k, t in self.tables.items() if t.file == name]:
            del self.tables[k]
    def file_names(self) -> set:
        return set(self._digests)
    def is_empty(self) -> bool:
        return not self.pdfs and not self.tables
    def _add_pdf(self, name: str, data: bytes) -> None:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ValueError("PDF is password-protected.")
        prefix = uuid.uuid4().hex[:8]
        docs, ids, metas = [], [], []
        empty, preview = 0, ""
        for page_no, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                empty += 1
                continue
            if not preview:
                preview = " ".join(text.split())[:300]
            for j, chunk in enumerate(_splitter.split_text(text)):
                docs.append(chunk)
                ids.append(f"{prefix}-{page_no}-{j}")
                metas.append({"file": name, "page": page_no})
        if not docs:
            raise ValueError("No extractable text (scanned PDF? it needs OCR first).")
        for i in range(0, len(docs), 128):
            self._collection.add(
                documents=docs[i:i + 128],
                ids=ids[i:i + 128],
                metadatas=metas[i:i + 128],
            )
        self.pdfs[name] = PdfInfo(name, len(reader.pages), len(docs), empty, preview)
    def search_pdfs(self, query: str, files: list, k_per_file: int = 4) -> list:
        out = []
        for f in files:
            info = self.pdfs.get(f)
            if not info:
                continue
            res = self._collection.query(
                query_texts=[query],
                n_results=min(k_per_file, info.chunks),
                where={"file": f},
            )
            for text, meta, dist in zip(
                res["documents"][0], res["metadatas"][0], res["distances"][0]
            ):
                out.append({"file": meta["file"], "page": meta["page"],
                            "text": text, "distance": dist})
        return out
    @staticmethod
    def _read_csv(data: bytes) -> pd.DataFrame:
        last = None
        for enc in ("utf-8-sig", "latin-1"):
            try:
                return pd.read_csv(io.BytesIO(data), sep=None, engine="python", encoding=enc)
            except UnicodeDecodeError as e:
                last = e
        raise ValueError(f"Could not decode CSV: {last}")
    def _add_tables(self, name: str, data: bytes) -> None:
        is_csv = name.lower().endswith(".csv")
        frames = {"": self._read_csv(data)} if is_csv else pd.read_excel(
            io.BytesIO(data), sheet_name=None
        )
        added = 0
        for sheet, df in frames.items():
            df = df.dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)
            if df.empty:
                continue
            df.columns = [str(c).strip() for c in df.columns]
            key = name if is_csv else f"{name}::{sheet}"
            self.tables[key] = TableInfo(key, name, sheet, df)
            added += 1
        if not added:
            raise ValueError("No data rows found.")
    def describe_table(self, key: str, detail: bool = True, sample_n: int = 3) -> str:
        df = self.tables[key].df
        cols = []
        for c in df.columns:
            s = df[c]
            info = f"{c} ({s.dtype})"
            if detail and (pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s)):
                n = s.nunique()
                if 0 < n <= 12:
                    info += " values=" + str(sorted(map(str, s.dropna().unique())))
            cols.append(info)
        sample = df.head(sample_n).astype(str).to_dict(orient="records")
        return f"- key={key!r}: {len(df)} rows; columns: " + "; ".join(cols) + f"; sample_rows={sample}"
    def catalog(self) -> str:
        lines = [
            f"- PDF {p.name!r}: {p.pages} pages. Starts with: {p.preview!r}"
            for p in self.pdfs.values()
        ]
        lines += [self.describe_table(k, detail=False, sample_n=1) for k in self.tables]
        return "\n".join(lines) or "(no files uploaded)"