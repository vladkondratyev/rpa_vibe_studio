"""Минимальный S3-клиент (AWS Signature V4) без boto3: Yandex Object Storage,
MinIO, VK Cloud, SberCloud, AWS и другие S3-совместимые хранилища."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

YANDEX_ENDPOINT = "https://storage.yandexcloud.net"
EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sign_request(method: str, url: str, headers: Dict[str, str], payload_hash: str, access_key: str,
                 secret_key: str, region: str, now: Optional[dt.datetime] = None,
                 service: str = "s3") -> Dict[str, str]:
    """Возвращает заголовки с подписью Authorization (SigV4)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    u = urlparse(url)
    host = u.netloc
    canonical_uri = quote(u.path or "/", safe="/~-_.")
    query = sorted((k, v) for k, _, v in (part.partition("=") for part in u.query.split("&") if part))
    canonical_query = "&".join(f"{quote(k, safe='~-_.')}={quote(v, safe='~-_.')}" for k, v in query)
    hdrs = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
    hdrs.update({"host": host, "x-amz-date": amz_date, "x-amz-content-sha256": payload_hash})
    signed = ";".join(sorted(hdrs))
    canonical = "\n".join([method, canonical_uri, canonical_query,
                           "".join(f"{k}:{hdrs[k]}\n" for k in sorted(hdrs)), signed, payload_hash])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _sign(_sign(_sign(_sign(("AWS4" + secret_key).encode(), day), region), service), "aws4_request")
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    out = {key: val for key, val in headers.items()}
    out.update({"x-amz-date": amz_date, "x-amz-content-sha256": payload_hash,
                "Authorization": f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, "
                                 f"Signature={signature}"})
    return out


class S3:
    def __init__(self, access_key: str, secret_key: str, endpoint: str = YANDEX_ENDPOINT,
                 region: str = "ru-central1", timeout: float = 300) -> None:
        self.access_key, self.secret_key = access_key, secret_key
        self.endpoint, self.region, self.timeout = endpoint.rstrip("/"), region, timeout

    def _url(self, bucket: str, key: str = "", query: str = "") -> str:
        path = f"/{bucket}" + (f"/{quote(key, safe='/~-_.')}" if key else "")
        return self.endpoint + path + (f"?{query}" if query else "")

    def request(self, method: str, bucket: str, key: str = "", query: str = "", body: bytes = b"",
                headers: Optional[Dict[str, str]] = None) -> Any:
        import httpx

        url = self._url(bucket, key, query)
        payload_hash = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA
        signed = sign_request(method, url, headers or {}, payload_hash, self.access_key, self.secret_key, self.region)
        r = httpx.request(method, url, content=body or None, headers=signed, timeout=self.timeout)
        if r.status_code >= 400:
            code = _xml_text(r.content, "Code") or r.status_code
            raise RuntimeError(f"S3 {method} {bucket}/{key}: {code} {_xml_text(r.content, 'Message') or ''}".strip())
        return r

    def put(self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.request("PUT", bucket, key, body=data, headers={"Content-Type": content_type})

    def get(self, bucket: str, key: str) -> bytes:
        return self.request("GET", bucket, key).content

    def delete(self, bucket: str, key: str) -> None:
        self.request("DELETE", bucket, key)

    def list(self, bucket: str, prefix: str = "", limit: int = 1000) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        token = ""
        while len(items) < limit:
            q = "list-type=2" + (f"&prefix={quote(prefix, safe='')}" if prefix else "")
            if token:
                q += f"&continuation-token={quote(token, safe='')}"
            root = ET.fromstring(self.request("GET", bucket, query=q).content)
            for c in root.iter():
                if c.tag.rsplit("}", 1)[-1] == "Contents":
                    d = {x.tag.rsplit("}", 1)[-1]: x.text for x in c}
                    items.append({"key": d.get("Key"), "size": int(d.get("Size") or 0),
                                  "modified": d.get("LastModified"), "etag": (d.get("ETag") or "").strip('"')})
            token = next((x.text for x in root.iter() if x.tag.rsplit("}", 1)[-1] == "NextContinuationToken"), "")
            if not token:
                break
        return items[:limit]

    def presign(self, bucket: str, key: str, expires: int = 3600) -> str:
        """Временная ссылка на скачивание (query-подпись)."""
        now = dt.datetime.now(dt.timezone.utc)
        amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        scope = f"{day}/{self.region}/s3/aws4_request"
        u = urlparse(self._url(bucket, key))
        params = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{self.access_key}/{scope}",
                  "X-Amz-Date": amz_date, "X-Amz-Expires": str(expires), "X-Amz-SignedHeaders": "host"}
        cq = "&".join(f"{quote(k, safe='~-_.')}={quote(v, safe='~-_.')}" for k, v in sorted(params.items()))
        canonical = "\n".join(["GET", quote(u.path, safe="/~-_."), cq, f"host:{u.netloc}\n", "host", "UNSIGNED-PAYLOAD"])
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        k = _sign(_sign(_sign(_sign(("AWS4" + self.secret_key).encode(), day), self.region), "s3"), "aws4_request")
        return f"{self.endpoint}{u.path}?{cq}&X-Amz-Signature={hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()}"


def _xml_text(content: bytes, tag: str) -> Optional[str]:
    try:
        for el in ET.fromstring(content).iter():
            if el.tag.rsplit("}", 1)[-1] == tag:
                return el.text
    except ET.ParseError:
        return None
    return None


def split_path(path: str) -> Tuple[str, str]:
    bucket, _, key = path.lstrip("/").partition("/")
    return bucket, key
