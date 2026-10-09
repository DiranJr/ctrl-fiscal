"""Verificação pelo gateway do AMBIENTE TEMPORÁRIO (8098, scripts/ambiente_descartavel.py): DANFSe, XML original, filtros de retenção, download em lote
além da primeira página (job + ticket de uso único por POST) e isolamento entre empresas.

Usa a conta fictícia operador.a do ambiente temporário (nunca impressa) e as empresas de demonstração que
ela enxerga. Não contata o governo e não transmite nada. Imprime só contagens, códigos HTTP e tamanhos.

Uso: .venv\\Scripts\\python -I scripts\\verificar_danfse_lote_gateway.py
"""
from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
B = "http://127.0.0.1:8098"


def main() -> int:
    pw = (ROOT / "secrets-descartavel" / "senha-operador.a.txt").read_text(encoding="utf-8").strip()
    c = httpx.Client(base_url=B, timeout=120, headers={"Origin": B})
    r = c.post("/api/core/v1/auth/login", json={"email": "operador.a@descartavel.example.com", "password": pw})
    assert r.status_code == 200, r.status_code
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    comps = c.get("/api/core/v1/auth/me", headers=h).json()["companies"]
    a = next(x for x in comps if x.get("environment") == "mock")
    other = next(x for x in comps if x["id"] != a["id"])
    F = f"/api/fiscal/v1/companies/{a['id']}"
    ok = True

    def check(label: str, cond: bool, info: str = "") -> None:
        nonlocal ok
        ok &= bool(cond)
        print(f"[{'PASS' if cond else 'FAIL'}] {label} {info}".rstrip())

    lst = c.get(f"{F}/documents", headers=h, params={"page_size": 100}).json()
    docs = lst["items"]
    with_xml = [d for d in docs if d.get("danfse_available")]
    check("listagem com retention_summary/danfse_available", all("retention_summary" in d for d in docs),
          f"total={lst['total']} com_danfse={len(with_xml)}")
    if with_xml:
        d = with_xml[0]
        pdf = c.get(f"{F}/documents/{d['id']}/danfse", headers=h)
        check("DANFSe 200 application/pdf inline", pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
              and pdf.headers["content-disposition"].startswith("inline"), f"bytes={len(pdf.content)}")
        check("DANFSe sem token na URL e sem cache", "token" not in str(pdf.url) and pdf.headers.get("cache-control") == "no-store")
        x = c.get(f"{F}/documents/{d['id']}/xml", headers=h)
        check("XML original 200 + nosniff", x.status_code == 200 and "nosniff" in x.headers.get_list("x-content-type-options"))
        check("DANFSe de outra empresa → 404", c.get(f"/api/fiscal/v1/companies/{other['id']}/documents/{d['id']}/danfse",
                                                    headers=h).status_code == 404)
        check("DANFSe sem autenticação → 401", c.get(f"{F}/documents/{d['id']}/danfse").status_code == 401)
    sim = [d for d in docs if d.get("simulated")]
    if sim:
        r = c.get(f"{F}/documents/{sim[0]['id']}/danfse", headers=h)
        check("documento simulado não gera DANFSe (409)", r.status_code == 409 and r.json().get("code") == "DANFSE_SIMULATED")

    counts = {k: c.get(f"{F}/documents", headers=h, params={"retention": k, "page_size": 1}).json()["total"]
              for k in ("WITH", "WITHOUT", "UNKNOWN")}
    check("filtros com/sem/desconhecida somam o total", sum(counts.values()) == lst["total"], json.dumps(counts))
    r = c.get(f"{F}/documents", headers=h, params=[("retention_type", "ISS"), ("retention_type", "IRRF")])
    check("2+ tipos sem regra explícita → 422", r.status_code == 422 and r.json().get("code") == "RETENTION_MATCH_REQUIRED")

    # Lote: todos os resultados do filtro (além da primeira página de 2), PDF+XML
    page = c.get(f"{F}/documents", headers=h, params={"page_size": 2}).json()
    xml_docs = [d for d in docs if d.get("xml_available")]
    r = c.post(f"{F}/documents/bundles", headers=h, json={"format": "XML", "filters": {}})
    check("lote XML de todos os resultados aceito (202)", r.status_code == 202, f"página=2 de total={page['total']}")
    job_id = r.json()["job_id"]
    for _ in range(120):
        job = c.get(f"{F}/jobs/{job_id}", headers=h).json()
        if job["status"] not in ("QUEUED", "RUNNING"):
            break
        time.sleep(1)
    check("job terminou", job["status"] in ("SUCCEEDED", "PARTIAL"), f"status={job['status']} result={json.dumps(job['result'])}")
    check("conjunto = todos os resultados (não só a página)", job["result"]["documents"] == page["total"])
    check("XML incluídos = notas com XML", job["result"]["xml_files"] == len(xml_docs) or page["total"] > 100,
          f"xml={job['result']['xml_files']} com_xml={len(xml_docs)}")
    t = c.post(f"{F}/jobs/{job_id}/download-ticket", headers=h).json()
    check("ticket: POST, campo no corpo, ação sem ticket na URL", t["method"] == "POST" and "ticket" not in t["action"])
    z = c.post(t["action"], data={"ticket": t["ticket"]})
    check("download por formulário (sem bearer) 200 zip", z.status_code == 200 and z.headers["content-type"] == "application/zip",
          f"bytes={len(z.content)}")
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    check("ZIP com manifesto.json e manifesto.csv", "manifesto.json" in names and "manifesto.csv" in names, f"entradas={len(names)}")
    check("ticket é de uso único", c.post(t["action"], data={"ticket": t["ticket"]}).status_code == 403)
    t2 = c.post(f"{F}/jobs/{job_id}/download-ticket", headers=h).json()
    check("ticket não vale para outra empresa", c.post(f"/api/fiscal/v1/companies/{other['id']}/jobs/{job_id}/download-file",
                                                       data={"ticket": t2["ticket"]}).status_code == 403)
    check("job de outra empresa → 404", c.get(f"/api/fiscal/v1/companies/{other['id']}/jobs/{job_id}", headers=h).status_code == 404)

    csrf = c.get("/api/core/v1/auth/csrf").json().get("csrf_token")
    c.post("/api/core/v1/auth/logout", headers={**h, "X-CSRF-Token": csrf or ""})
    print("RESULTADO:", "OK" if ok else "FALHAS")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
