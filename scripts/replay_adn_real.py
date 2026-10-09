"""Reproduz os lotes ADN REAIS já baixados (somente leitura) pelo motor de sincronização da plataforma, num banco
SQLite temporário, com o governo SIMULADO (httpx.MockTransport servindo exatamente os lotes gravados).

Uso: .venv\\Scripts\\python -I scripts\\replay_adn_real.py

Classificação: TESTE SIMULADO com dados reais — não é chamada governamental. Verifica persistência, cursor por
ambiente, eventos (cancelamentos), deduplicação em replay após 12 h e seleção pelo CNPJ exato. Imprime só
contagens; o banco temporário é descartado.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from conftest import SERVICES, SP, Stack, _migrate  # noqa: E402,F401
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from ctrl_common.clock import FakeClock  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from test_official_integration import FakeGov  # noqa: E402

PROD = "adn.nfse.gov.br"
REST = "adn.producaorestrita.nfse.gov.br"


def load(path_glob: str) -> list[dict]:
    docs: dict[int, dict] = {}
    for f in sorted(ROOT.glob(path_glob)):
        for it in json.loads(f.read_text(encoding="utf-8-sig")).get("LoteDFe") or []:
            docs[int(it["NSU"])] = it
    return [docs[k] for k in sorted(docs)]


def main() -> int:
    perfil = json.loads((ROOT / "data/homologacao/empresa.json").read_text(encoding="utf-8"))
    gov = FakeGov()
    gov.adn[PROD] = load("data/oficial/producao/adn/lote-*.json")
    gov.adn[REST] = load("data/oficial/producao-restrita/adn-dfe-nsu0-lote.json")
    tmp = Path(tempfile.mkdtemp(prefix="ctrl-replay-"))
    urls = {svc: f"sqlite:///{tmp.as_posix()}/{svc}.db" for svc in SERVICES}
    for svc, url in urls.items():
        _migrate(svc, url)
    key = Ed25519PrivateKey.generate()
    keys = (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
            key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode())
    clock = FakeClock(datetime(2026, 10, 7, 17, 0, tzinfo=SP))
    from test_nfse_connector import TEST_TRUST

    st = Stack(urls, keys, clock, tmp, official={"vault_key_b64": base64.b64encode(os.urandom(32)).decode(),
                                                 "restricted_enabled": True, "production_distribution_enabled": True,
                                                 "http_factory": gov.factory, **TEST_TRUST})
    from core_api.models import Company
    from nfse_api import cert_store
    from nfse_api.vault import Vault
    from cryptography.hazmat.primitives.serialization import pkcs12
    from test_nfse_connector import make_cert

    cid = st.add_company("Empresa (replay)", perfil["cnpj"], environment="restricted", municipality=perfil["municipality_code"])
    with st.core_sessions() as s, s.begin():
        s.get(Company, cid).adn_environments = ["production", "restricted"]
    k, c = make_cert(cnpj_value=perfil["cnpj"])
    pfx = pkcs12.serialize_key_and_certificates(b"t", k, c, None, serialization.BestAvailableEncryption(b"replay-local"))
    cert_store.install(st.nfse_sessions, Vault(st.nfse_settings.vault_key_b64), company_id=cid, cnpj=perfil["cnpj"],
                       environments=["restricted", "production"], pfx=pfx, password=b"replay-local", now=clock.now(),
                       validator=st.nfse_settings.icp_validator())

    from ctrl_fiscal.models import DfeItem, Document, DocumentEvent, SyncSchedule

    w = st.worker()
    w.refresh_companies(force=True)

    def drain() -> None:
        for _ in range(400):
            if not w.run_sync():
                clock.advance(seconds=31)  # continuação de drenagem (DRAINING) é agendada para +30 s
                with st.fiscal_sessions() as s:
                    if not s.scalar(select(func.count()).select_from(SyncSchedule).where(SyncSchedule.status == "DRAINING")):
                        return

    drain()
    calls_first = len(gov.calls)

    def report(tag: str) -> None:
        with st.fiscal_sessions() as s:
            for env in ("restricted", "production"):
                sc = s.scalar(select(SyncSchedule).where(SyncSchedule.company_id == cid, SyncSchedule.environment == env))
                items = s.scalars(select(DfeItem).where(DfeItem.company_id == cid, DfeItem.environment == env)).all()
                docs = s.scalars(select(Document).where(Document.company_id == cid, Document.environment == env)).all()
                evs = s.scalar(select(func.count()).select_from(DocumentEvent).where(DocumentEvent.company_id == cid,
                                                                                      DocumentEvent.environment == env))
                print(f"[{tag}] {env}: agenda={sc.status} cursor_NSU={sc.cursor_nsu} intervalo={sc.interval_hours}h "
                      f"itens={len(items)} parse={dict(Counter(i.parse_status for i in items))} "
                      f"tipos={dict(Counter(i.doc_kind for i in items))} papel={dict(Counter(i.company_role for i in items))}")
                print(f"[{tag}] {env}: documentos={len(docs)} direção={dict(Counter(d.direction for d in docs))} "
                      f"situação={dict(Counter(d.status for d in docs))} eventos_gravados={evs} "
                      f"simulados={sum(1 for d in docs if d.simulated)}")
                c101 = [d for d in docs if d.status_code == "101"]
                print(f"[{tag}] {env}: cStat101={len(c101)} situação={dict(Counter(d.status for d in c101))} "
                      f"com_vínculo_substituída={sum(1 for d in c101 if d.substitutes_access_key)} | "
                      f"substituídas_vinculadas={sum(1 for d in docs if d.substituted_by_access_key)} "
                      f"canceladas_por={dict(Counter(d.cancellation_event_type for d in docs if d.status == 'CANCELLED'))} "
                      f"UNKNOWN_por_cStat={dict(Counter(d.status_code for d in docs if d.status == 'UNKNOWN'))}")

    report("ciclo inicial")
    adn_hosts = Counter(c[2] for c in gov.calls if c[0] == "adn")
    cnpj_ok = all(f"cnpjConsulta={perfil['cnpj']}" in c[4] for c in gov.calls if c[0] == "adn")
    print(f"chamadas simuladas ao ADN por host: {dict(adn_hosts)}; cnpjConsulta = CNPJ exato em todas: {cnpj_ok}; "
          f"métodos: {sorted({c[1] for c in gov.calls})}")
    clock.advance(hours=12, minutes=1)
    drain()
    print(f"replay após 12 h: novas chamadas={len(gov.calls) - calls_first} "
          f"(última consulta de cada ambiente devolve E2220)")
    report("após replay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
