"""Emissão de TESTE em Produção Restrita pela API da plataforma (gateway), a partir de data/homologacao/nota-teste.json.

Uso:
  .venv\\Scripts\\python scripts\\emitir_nota_teste.py --empresa <id> [--lote] [--confirmar]

- Sem --confirmar: cria/reaproveita o cliente e o rascunho, executa a validação e PARA (nada é agendado).
- Com --confirmar: agenda a nota para 1 minuto à frente (fuso da empresa) e acompanha o lote até o estado final.
- --lote: em vez de uma nota, agenda `lote_agendado.quantidade` cópias para `lote_agendado.scheduled_at`.
- Usa a conta informada (--email/--senha-arquivo), autorizada pelo titular, sem permissão admin; nunca a conta pessoal.
- Recusa empresa fora de "restricted" (produção nunca). Imprime apenas estados, códigos e Ids internos —
  nunca CNPJ/CPF, nomes, valores ou chaves de acesso.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]


def clean(obj):  # noqa: ANN001, ANN201
    """Remove chaves de ajuda ('_...') e grupos inteiramente vazios."""
    if isinstance(obj, dict):
        out = {k: clean(v) for k, v in obj.items() if not k.startswith("_")}
        out = {k: v for k, v in out.items() if v not in (None, {}, [])}
        return out or None
    return obj


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--empresa", required=True)
    ap.add_argument("--nota", default="data/homologacao/nota-teste.json")
    ap.add_argument("--email", required=True, help="Conta autorizada pelo titular para a homologação oficial.")
    ap.add_argument("--senha-arquivo", required=True, help="Arquivo local com a senha (nunca impresso).")
    ap.add_argument("--base", default="http://127.0.0.1:8088")
    ap.add_argument("--lote", action="store_true")
    ap.add_argument("--confirmar", action="store_true")
    a = ap.parse_args(argv)
    spec = json.loads((ROOT / a.nota).read_text(encoding="utf-8"))
    c = httpx.Client(base_url=a.base, timeout=60)
    r = c.post("/api/core/v1/auth/login", headers={"Origin": a.base},
               json={"email": a.email, "password": (ROOT / a.senha_arquivo).read_text(encoding="utf-8").strip()})
    r.raise_for_status()
    h = {"Authorization": "Bearer " + r.json()["access_token"]}
    core, fiscal = f"/api/core/v1/companies/{a.empresa}", f"/api/fiscal/v1/companies/{a.empresa}"

    prof = c.get(f"{core}/fiscal-profile", headers=h).json()
    if prof.get("environment") != "restricted":
        print(f"recusado: empresa em '{prof.get('environment')}' (somente restricted)")
        return 2
    st = c.get(f"{core}/integration-status", headers=h).json()
    print(f"integração restricted: status={st['status']} emissão={st['capabilities'].get('emission')} "
          f"bloqueios={[b['code'] for b in st['blockers']]}")
    can_emit = bool(st["capabilities"].get("emission"))
    if not can_emit:
        print("emissão bloqueada: segue só com cadastro, rascunho e validação (nada será agendado).")

    toma = clean(spec["tomador"])
    found = c.get(f"{core}/customers", headers=h, params={"query": toma["document"], "page_size": 5}).json()["items"]
    cust = next((x for x in found if x["document"] == toma["document"]), None)
    if cust is None:
        r = c.post(f"{core}/customers", headers=h, json=toma)
        if r.status_code != 201:
            print(f"cliente recusado: HTTP {r.status_code} {r.json().get('code')} {[e.get('field') for e in r.json().get('errors') or []]}")
            return 4
        cust = r.json()
    print(f"cliente: id={cust['id']} ({'existente' if found else 'criado'})")

    nota = clean(spec["nota"])
    nota["customer_id"] = cust["id"]
    r = c.post(f"{fiscal}/drafts", headers=h, json=nota)
    if r.status_code != 201:
        print(f"rascunho recusado: HTTP {r.status_code} {r.json().get('code')} {[e.get('field') for e in r.json().get('errors') or []]}")
        return 5
    draft = r.json()
    v = c.post(f"{fiscal}/drafts/{draft['id']}/validate", headers=h).json()
    print(f"rascunho: id={draft['id']} válido={v['valid']}")
    for i in v["issues"]:
        print(f"  {i['severity']}: {i['field']} {i['code']}")
    if not v["valid"] or not a.confirmar or not can_emit:
        print("corrija os erros acima." if not v["valid"] else
              "parado antes de agendar: integração sem emissão." if not can_emit else "parado antes de agendar (use --confirmar).")
        return 0 if v["valid"] else 6

    drafts = [draft]
    tz = ZoneInfo(prof["timezone"])
    if a.lote:
        lote = spec["lote_agendado"]
        for _ in range(int(lote["quantidade"]) - 1):
            drafts.append(c.post(f"{fiscal}/drafts", headers=h, json=nota).json())
        when = datetime.fromisoformat(lote["scheduled_at"])
    else:
        when = (datetime.now(timezone.utc) + timedelta(minutes=1)).astimezone(tz).replace(microsecond=0)
    r = c.post(f"{fiscal}/batches", headers={**h, "Idempotency-Key": uuid.uuid4().hex}, json={
        "drafts": [{"id": d["id"], "expected_version": d["version"]} for d in drafts],
        "scheduled_at": when.isoformat(), "timezone": prof["timezone"]})
    if r.status_code != 202:
        print(f"lote recusado: HTTP {r.status_code} {r.json().get('code')}")
        return 7
    bid = r.json()["batch_id"]
    print(f"lote: id={bid} itens={len(drafts)} agendado_local={when.astimezone(tz).isoformat()}")
    deadline = time.time() + max(600, (when - datetime.now(timezone.utc)).total_seconds() + 600)
    while time.time() < deadline:
        b = c.get(f"{fiscal}/batches/{bid}", headers=h).json()
        states = [i["status"] for i in b["items"]]
        if all(s in ("AUTHORIZED", "REJECTED", "WITHDRAWN") for s in states):
            break
        time.sleep(10)
    for i in b["items"]:
        res = i.get("result") or {}
        print(f"  item dps={i['dps_id']} status={i['status']} simulada={res.get('simulated')} "
              f"erros={[e['code'] for e in res.get('errors') or []]} ultimo_erro={(i.get('last_error') or {}).get('code')}")
    return 0


def _run(argv: list[str]) -> int:
    """Executa e encerra a sessão aberta (logout), mesmo em erro."""
    global httpx  # noqa: PLW0603
    real_client = httpx.Client
    opened: list[httpx.Client] = []

    def tracking_client(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        c = real_client(*args, **kwargs)
        opened.append(c)
        return c

    httpx.Client = tracking_client  # type: ignore[assignment]
    try:
        return main(argv)
    finally:
        httpx.Client = real_client  # type: ignore[assignment]
        for c in opened:
            try:
                base = str(c.base_url).rstrip("/")
                csrf = c.get("/api/core/v1/auth/csrf").json().get("csrf_token")
                if csrf:
                    c.post("/api/core/v1/auth/logout", headers={"Origin": base, "X-CSRF-Token": csrf})
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    raise SystemExit(_run(sys.argv[1:]))
