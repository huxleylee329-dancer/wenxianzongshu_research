"""Public scholarly APIs with separate discovery and evidence projections.

Metadata-only records are kept by search_records for DOI/fulltext resolution;
they are never converted into synthetic evidence by search/search_candidates.
"""

from __future__ import annotations

from urllib.parse import quote

from gpt_researcher.academic_library.http import PublicHTTP
from gpt_researcher.academic_library.models import Paper, LibraryError, doi, public_url, text
from gpt_researcher.screening.models import PaperCandidate, build_candidate_id
from .academic_utils import format_academic_body


def _dict(value):
    return value if type(value) is dict else {}


def _list(value):
    return value if type(value) is list else []


def _first(value):
    return value[0] if type(value) is list and value else value


def _year(value):
    value = str(value)[:4] if type(value) in (str, int) else ""
    return int(value) if value.isdigit() and 1000 <= int(value) <= 9999 else None


class ScholarlySearch:
    BODY_IS_PREFETCHED_CONTENT = True
    provider = ""

    def __init__(self, query: str, query_domains=None, *, http=None):
        self.query = query
        self.http = PublicHTTP() if http is None else http

    def search_records(self, max_results: int = 10) -> tuple[Paper, ...]:
        if type(self.query) is not str or not self.query.strip() or len(self.query) > 4096:
            raise LibraryError("invalid_query")
        if type(max_results) is not int or not 1 <= max_results <= 50:
            raise LibraryError("invalid_result_limit")
        payload = self._request(max_results)
        records = []
        for row in self._rows(payload)[:max_results]:
            if type(row) is not dict:
                continue
            try:
                paper = self._paper(row)
            except (TypeError, ValueError, KeyError, IndexError):
                continue
            if paper.title and public_url(paper.url) and paper.record_id:
                records.append(paper)
        return tuple(records)

    def search_candidates(self, max_results: int = 10) -> tuple[PaperCandidate, ...]:
        return tuple(
            PaperCandidate(
                candidate_id=build_candidate_id(source=self.provider, source_record_id=p.record_id, doi=p.doi, href=p.url),
                source=self.provider, source_record_id=p.record_id,
                retrieval_query=self.query, source_rank=index,
                title=p.title, href=p.url, abstract=p.abstract,
                body=format_academic_body(title=p.title, authors=p.authors, year=p.year, venue=None, doi=p.doi, source=self.provider, abstract=p.abstract),
                doi=p.doi, authors=p.authors, published_year=p.year,
                publication_types=(p.publication_type,),
            )
            for index, p in enumerate(self.search_records(max_results), 1) if p.abstract
        )

    def search(self, max_results: int = 10):
        return [item.to_retriever_result() for item in self.search_candidates(max_results)]


class HALSearch(ScholarlySearch):
    provider = "hal"

    def _request(self, limit):
        # Quote the whole query: user text is not executable Solr syntax.
        escaped = self.query.replace("\\", "\\\\").replace('"', '\\"')
        return self.http.json("https://api.archives-ouvertes.fr/search/", {"q": 'text:"' + escaped + '"', "rows": limit, "wt": "json", "fl": "halId_s,title_s,abstract_s,doiId_s,authFullName_s,producedDateY_i,uri_s,fileMain_s,docType_s,licence_s"})

    def _rows(self, payload):
        return _list(_dict(_dict(payload).get("response")).get("docs"))

    def _paper(self, row):
        url = public_url(row.get("fileMain_s"))
        return Paper(self.provider, text(row.get("halId_s"), 200), text(_first(row.get("title_s")), 512), public_url(row.get("uri_s")), text(_first(row.get("abstract_s"))), doi(row.get("doiId_s")), _year(row.get("producedDateY_i")), tuple(text(a, 200) for a in _list(row.get("authFullName_s"))), {"ART": "journalarticle", "COMM": "conference", "REPORT": "report", "UNDEFINED": "preprint"}.get(row.get("docType_s"), "unknown"), (url,) if url else (), text(row.get("licence_s"), 512) or None)


class OSTISearch(ScholarlySearch):
    provider = "osti"

    def _request(self, limit):
        return self.http.json("https://www.osti.gov/api/v1/records", {"q": self.query, "rows": limit, "page": 1})

    def _rows(self, payload):
        return _list(payload)

    def _paper(self, row):
        record_id = str(row.get("osti_id", ""))
        links = tuple(public_url(item.get("href")) for item in _list(row.get("links")) if type(item) is dict and item.get("rel") == "fulltext")
        return Paper(self.provider, record_id, text(row.get("title"), 512), "https://www.osti.gov/biblio/" + quote(record_id, safe=""), text(row.get("description")), doi(row.get("doi")), _year(row.get("publication_date")), tuple(text(a, 200) for a in _list(row.get("authors"))), "journalarticle" if row.get("product_type") == "Journal Article" else "report", tuple(x for x in links if x))


class OpenAIRESearch(ScholarlySearch):
    provider = "openaire"

    def _request(self, limit):
        return self.http.json("https://api.openaire.eu/graph/v3/research-products", {"search": self.query, "type": "publication", "pageSize": limit, "page": 1})

    def _rows(self, payload):
        return _list(_dict(payload).get("results"))

    def _paper(self, row):
        record_id = text(row.get("id"), 256)
        identifiers = _list(row.get("pids"))
        paper_doi = next((doi(p.get("value")) for p in identifiers if type(p) is dict and p.get("scheme") == "doi"), None)
        urls = []
        for instance in _list(row.get("instances")):
            if type(instance) is dict and _dict(instance.get("accessRight")).get("label", "").lower() == "open access":
                urls.extend(public_url(url) for url in _list(instance.get("urls")))
        return Paper(self.provider, record_id, text(row.get("mainTitle"), 512), "https://doi.org/" + paper_doi if paper_doi else "https://explore.openaire.eu/search/publication?pid=" + quote(record_id, safe=""), text(_first(row.get("descriptions"))), paper_doi, _year(row.get("publicationDate")), tuple(text(a.get("fullName"), 200) for a in _list(row.get("authors")) if type(a) is dict), "unknown", tuple(x for x in urls if x))


class CrossrefSearch(ScholarlySearch):
    provider = "crossref"

    def _request(self, limit):
        return self.http.json("https://api.crossref.org/works", {"query.bibliographic": self.query, "rows": limit})

    def _rows(self, payload):
        return _list(_dict(_dict(payload).get("message")).get("items"))

    def _paper(self, row):
        paper_doi = doi(row.get("DOI"))
        date = _first(_dict(row.get("published")).get("date-parts"))
        return Paper(self.provider, paper_doi or "", text(_first(row.get("title")), 512), public_url(row.get("URL")), text(row.get("abstract")), paper_doi, _year(_first(date)), tuple(text(" ".join((a.get("given", ""), a.get("family", ""))), 200) for a in _list(row.get("author")) if type(a) is dict), {"journal-article": "journalarticle", "proceedings-article": "conference", "posted-content": "preprint", "report": "report"}.get(row.get("type"), "unknown"))

    def resolve_doi(self, value: str) -> Paper:
        normalized = doi(value)
        if normalized is None:
            raise LibraryError("invalid_doi")
        result = self.http.json("https://api.crossref.org/works/" + quote(normalized, safe=""))
        return self._paper(_dict(_dict(result).get("message")))


class EuropePMCSearch(ScholarlySearch):
    provider = "europe_pmc"

    def _request(self, limit):
        return self.http.json("https://www.ebi.ac.uk/europepmc/webservices/rest/search", {"query": self.query, "format": "json", "resultType": "core", "pageSize": limit})

    def _rows(self, payload):
        return _list(_dict(_dict(payload).get("resultList")).get("result"))

    def _paper(self, row):
        record_id = text(row.get("id"), 128)
        source = text(row.get("source"), 16)
        pmcid = row.get("pmcid")
        urls = ("https://www.ebi.ac.uk/europepmc/webservices/rest/" + quote(pmcid, safe="") + "/fullTextXML",) if type(pmcid) is str and row.get("isOpenAccess") == "Y" else ()
        return Paper(self.provider, source + ":" + record_id, text(row.get("title"), 512), "https://europepmc.org/article/" + quote(source, safe="") + "/" + quote(record_id, safe=""), text(row.get("abstractText")), doi(row.get("doi")), _year(row.get("pubYear")), tuple(text(a.get("fullName"), 200) for a in _list(_dict(row.get("authorList")).get("author")) if type(a) is dict), "unknown", urls)


class ZenodoSearch(ScholarlySearch):
    provider = "zenodo"

    def _request(self, limit):
        escaped = self.query.replace("\\", "\\\\").replace('"', '\\"')
        return self.http.json("https://zenodo.org/api/records", {"q": '("' + escaped + '") AND resource_type.type:publication', "size": limit, "page": 1})

    def _rows(self, payload):
        return _list(_dict(_dict(payload).get("hits")).get("hits"))

    def _paper(self, row):
        meta = _dict(row.get("metadata"))
        kind = _dict(meta.get("resource_type"))
        if kind.get("type") != "publication":
            raise ValueError("not a publication")
        urls = tuple(public_url(_dict(f.get("links")).get("self")) for f in _list(row.get("files")) if type(f) is dict and str(f.get("key", "")).lower().endswith(".pdf") and meta.get("access_right") == "open")
        record_id = str(row.get("id", ""))
        return Paper(self.provider, record_id, text(meta.get("title"), 512), "https://zenodo.org/records/" + quote(record_id, safe=""), text(meta.get("description")), doi(meta.get("doi")), _year(meta.get("publication_date")), tuple(text(a.get("name"), 200) for a in _list(meta.get("creators")) if type(a) is dict), {"article": "journalarticle", "preprint": "preprint", "report": "report"}.get(kind.get("subtype"), "unknown"), tuple(x for x in urls if x), text(_dict(meta.get("license")).get("id"), 512) or None)


SCHOLARLY_RETRIEVERS = {
    "hal": HALSearch, "osti": OSTISearch, "openaire": OpenAIRESearch,
    "crossref": CrossrefSearch, "europe_pmc": EuropePMCSearch, "zenodo": ZenodoSearch,
}
