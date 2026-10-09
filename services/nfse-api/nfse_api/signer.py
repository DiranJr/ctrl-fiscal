"""Assinatura XMLDSig enveloped da DPS conforme perfil CONFIRMADO do XSD 1.00.

Perfil (Schemas/1.00/xmldsig-core-schema.xsd, docs/oficial/):
- CanonicalizationMethod: http://www.w3.org/TR/2001/REC-xml-c14n-20010315
- SignatureMethod: http://www.w3.org/2000/09/xmldsig#rsa-sha1
- DigestMethod: http://www.w3.org/2000/09/xmldsig#sha1
- Transforms: enveloped-signature + c14n; Reference/@URI; KeyInfo/X509Data/X509Certificate.
- Signature como filho de DPS após infDPS, SEM prefixo de namespace (E1228).
Para versao="1.01" o perfil foi confirmado por DPS aceitas pela Sefin (ver CONFIRMED_VERSIONS).
"""
from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree

DS = "http://www.w3.org/2000/09/xmldsig#"
C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
RSA_SHA1 = DS + "rsa-sha1"
SHA1 = DS + "sha1"
ENVELOPED = DS + "enveloped-signature"
# 1.00: perfil fixado no XSD 1.00. 1.01: o XSD usa xmldsig genérico; o perfil foi confirmado empiricamente em
# documentos ACEITOS pela Sefin: DPS assinadas da Produção Restrita (3 de 2026) e pedidos de evento enviados por API
# por outros emitentes em produção (21 pedRegEvento aceitos, 7 deles v1.01 em 2026), todos com c14n 20010315,
# rsa-sha1/sha1, transforms enveloped+c14n, Reference URI="#Id" e Signature sem prefixo. (As notas da própria empresa
# foram emitidas pelo Emissor Web e não trazem assinatura na DPS.)
CONFIRMED_VERSIONS = ("1.00", "1.01")


class SignatureProfileUnconfirmed(Exception):
    pass


def _parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, remove_blank_text=False)


def _c14n(el: etree._Element) -> bytes:
    return etree.tostring(el, method="c14n", exclusive=False, with_comments=False)


def _signed_target(root: etree._Element) -> etree._Element:
    target = root[0] if len(root) else None
    if target is None or not target.get("Id"):
        raise ValueError("Elemento assinado (primeiro filho com Id) ausente")
    return target


def sign_enveloped(xml: bytes, private_key: rsa.RSAPrivateKey, cert_der: bytes) -> bytes:
    return sign_enveloped_with(xml, cert_der,
                               lambda data: private_key.sign(data, padding.PKCS1v15(), hashes.SHA1()))  # noqa: S303


def sign_enveloped_with(xml: bytes, cert_der: bytes, sign_sha1: Callable[[bytes], bytes]) -> bytes:
    """Mesmo perfil, com a operação RSA-SHA1 PKCS#1 v1.5 delegada (ex.: chave não exportável de um
    repositório de certificados). `sign_sha1` recebe o SignedInfo canonicalizado e devolve a assinatura."""
    root = etree.fromstring(xml, _parser())
    version = root.get("versao")
    if version not in CONFIRMED_VERSIONS:
        raise SignatureProfileUnconfirmed(f"Perfil de assinatura para versao={version} não confirmado")
    target = _signed_target(root)
    digest = base64.b64encode(hashlib.sha1(_c14n(target)).digest()).decode()  # noqa: S324 - exigido pelo XSD 1.00

    sig = etree.SubElement(root, f"{{{DS}}}Signature", nsmap={None: DS})
    si = etree.SubElement(sig, f"{{{DS}}}SignedInfo")
    etree.SubElement(si, f"{{{DS}}}CanonicalizationMethod", Algorithm=C14N)
    etree.SubElement(si, f"{{{DS}}}SignatureMethod", Algorithm=RSA_SHA1)
    ref = etree.SubElement(si, f"{{{DS}}}Reference", URI="#" + target.get("Id"))
    tr = etree.SubElement(ref, f"{{{DS}}}Transforms")
    etree.SubElement(tr, f"{{{DS}}}Transform", Algorithm=ENVELOPED)
    etree.SubElement(tr, f"{{{DS}}}Transform", Algorithm=C14N)
    etree.SubElement(ref, f"{{{DS}}}DigestMethod", Algorithm=SHA1)
    etree.SubElement(ref, f"{{{DS}}}DigestValue").text = digest
    value = sign_sha1(_c14n(si))
    etree.SubElement(sig, f"{{{DS}}}SignatureValue").text = base64.b64encode(value).decode()
    ki = etree.SubElement(sig, f"{{{DS}}}KeyInfo")
    xd = etree.SubElement(ki, f"{{{DS}}}X509Data")
    etree.SubElement(xd, f"{{{DS}}}X509Certificate").text = base64.b64encode(cert_der).decode()
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def verify_enveloped(xml: bytes) -> bool:
    """Verificação local (testes/diagnóstico); não substitui a validação oficial."""
    from cryptography import x509

    root = etree.fromstring(xml, _parser())
    sig = root.find(f"{{{DS}}}Signature")
    if sig is None:
        return False
    target = _signed_target(root)
    ref = sig.find(f"{{{DS}}}SignedInfo/{{{DS}}}Reference")
    if ref is None or ref.get("URI") != "#" + target.get("Id"):
        return False
    root.remove(sig)  # transform enveloped-signature
    expected = base64.b64encode(hashlib.sha1(_c14n(target)).digest()).decode()  # noqa: S324
    root.append(sig)
    if ref.findtext(f"{{{DS}}}DigestValue") != expected:
        return False
    cert = x509.load_der_x509_certificate(base64.b64decode(sig.findtext(f"{{{DS}}}KeyInfo/{{{DS}}}X509Data/{{{DS}}}X509Certificate")))
    try:
        cert.public_key().verify(
            base64.b64decode(sig.findtext(f"{{{DS}}}SignatureValue")),
            _c14n(sig.find(f"{{{DS}}}SignedInfo")), padding.PKCS1v15(), hashes.SHA1(),  # noqa: S303
        )
    except Exception:
        return False
    return True


def private_key_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
