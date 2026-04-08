#!/usr/bin/env python3
"""
normattiva_api_client.py

Lightweight wrapper around the live Normattiva API.
Use for real-time queries, single collections, or filtered requests.
Falls back gracefully if API is down; calls can use cached mirror as backup.

Usage:
  from normattiva_api_client import NormattivaAPI
  api = NormattivaAPI()
  collections = api.get_collection_catalogue()
  acts = api.get_collection("DPR", variant="O", format="akn")  # bytes
"""

import urllib.request
import urllib.parse
import urllib.error
import json
import time

BASE_API = "https://api.normattiva.it/t/normattiva.api/bff-opendata/v1"
UA = "NormattivaClient/1.0 (research mirror; contact redazione@normattiva.it)"


class NormattivaAPI:
    """Client for live Normattiva data access."""

    def __init__(self, timeout_s=30, retries=2):
        self.timeout = timeout_s
        self.retries = retries
        self.session_cookies = {}

    def _cookie_header(self):
        if self.session_cookies:
            return "; ".join(f"{k}={v}" for k, v in self.session_cookies.items())
        return None

    def _absorb_cookies(self, response):
        for raw_hdr, val in response.headers.items():
            if raw_hdr.lower() == "set-cookie":
                kv = val.split(";")[0].split("=", 1)
                if len(kv) == 2:
                    self.session_cookies[kv[0].strip()] = kv[1].strip()

    def _request(self, method, path, data=None, label=""):
        """Make HTTP request with retry logic."""
        url = f"{BASE_API}{path}"
        for attempt in range(1, self.retries + 1):
            hdrs = {
                "User-Agent": UA,
                "Referer": "https://dati.normattiva.it/",
                "Accept": "*/*",
            }
            ck = self._cookie_header()
            if ck:
                hdrs["Cookie"] = ck

            req = urllib.request.Request(
                url,
                data=data.encode("utf-8") if isinstance(data, str) else data,
                headers=hdrs,
                method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    self._absorb_cookies(r)
                    return r.read()
            except urllib.error.HTTPError as e:
                if attempt == self.retries:
                    raise
                time.sleep(2 * attempt)
            except Exception as e:
                if attempt == self.retries:
                    raise RuntimeError(f"{label} failed: {e}")
                time.sleep(2 * attempt)

    def get_collection_catalogue(self):
        """
        Fetch all pre-configured collections.
        Returns list of {nome, formatoCollezione, numeroAtti, dataCreazione}.
        """
        data = self._request(
            "GET", "/api/v1/collections/collection-predefinite", label="catalogue"
        )
        return json.loads(data.decode("utf-8"))

    def get_collection(self, nome, variant="O", format="AKN"):
        """
        Download a single collection ZIP as bytes.
        variant: 'O' (originale), 'M' (multivigente), 'V' (vigente)
        format: 'AKN', 'XML', 'PDF', etc.
        Returns (bytes, etag, content_type).
        """
        params = urllib.parse.urlencode(
            {"nome": nome, "formato": format, "formatoRichiesta": variant}
        )
        path = f"/api/v1/collections/download/collection-preconfezionata?{params}"
        try:
            # We need to use urlopen directly to get headers
            hdrs = {
                "User-Agent": UA,
                "Referer": "https://dati.normattiva.it/",
            }
            ck = self._cookie_header()
            if ck:
                hdrs["Cookie"] = ck
            req = urllib.request.Request(f"{BASE_API}{path}", headers=hdrs)
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                self._absorb_cookies(r)
                etag = r.headers.get("x-etag") or r.headers.get("ETag")
                content_type = r.headers.get("Content-Type", "application/octet-stream")
                data = r.read()
                return data, etag, content_type
        except Exception as e:
            raise RuntimeError(
                f"Failed to download {nome} ({variant}, {format}): {e}"
            )

    def get_extensions(self):
        """Get list of available export formats."""
        data = self._request("GET", "/api/v1/tipologiche/estensioni", label="formats")
        return json.loads(data.decode("utf-8"))

    def check_collection_etag(self, nome, variant="O", format="AKN"):
        """
        Get the ETag for a collection without downloading.
        Useful for checking if mirror is stale.
        Returns etag string or None.
        """
        params = urllib.parse.urlencode(
            {"nome": nome, "formato": format, "formatoRichiesta": variant}
        )
        path = f"/api/v1/collections/download/collection-preconfezionata?{params}"
        try:
            hdrs = {
                "User-Agent": UA,
                "Referer": "https://dati.normattiva.it/",
            }
            ck = self._cookie_header()
            if ck:
                hdrs["Cookie"] = ck
            req = urllib.request.Request(f"{BASE_API}{path}", headers=hdrs)
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.headers.get("x-etag") or r.headers.get("ETag")
        except Exception:
            return None

    def get_vigente(self, nome, format="AKN"):
        """
        Get VIGENTE variant (current law in force with amendments applied).
        
        Use this for:
        - Real-time compliance checking
        - "What does the law say now?" queries
        - Legal lookups that demand freshness
        - Lawyer/regulatory work
        
        Always hits live API (never cached) to ensure freshness.
        Returns (bytes, metadata_dict).
        """
        data, etag, ct = self.get_collection(nome, variant="V", format=format)
        return data, {
            "variant": "V",
            "format": format,
            "etag": etag,
            "content_type": ct,
            "note": "Always fresh from live API"
        }

    def get_multivigenza(self, nome, format="AKN"):
        """
        Get MULTIVIGENZA variant (all versions with full legislative history).
        
        Use this for:
        - Compliance audits ("Was this in effect on date X?")
        - Historical analysis (how has law changed)
        - Amendment tracking (all modifications)
        - Retroactive enforcement checks
        
        WARNING: Large file (2-3× bigger than ORIGINALE).
        WARNING: May be slow (5-10 seconds) or fail with HTTP 500.
        
        Returns (bytes, metadata_dict).
        """
        try:
            data, etag, ct = self.get_collection(nome, variant="M", format=format)
            return data, {
                "variant": "M",
                "format": format,
                "etag": etag,
                "content_type": ct,
                "note": "Full history with amendments; large file",
                "available": True
            }
        except Exception as e:
            # M variant sometimes fails with HTTP 500 on large files
            raise RuntimeError(
                f"Multivigenza unavailable for {nome} (known server issue on large files). "
                f"Try ORIGINALE instead: {e}"
            )

    def get_originale(self, nome, format="AKN"):
        """
        Get ORIGINALE variant (official text as published, no amendments).
        
        Use this for:
        - Reference/legal research
        - Historical snapshots
        - The "canonical" text
        - Academic/citation work
        
        Stable and reliable. This variant is mirrored locally on HuggingFace.
        Returns (bytes, metadata_dict).
        """
        data, etag, ct = self.get_collection(nome, variant="O", format=format)
        return data, {
            "variant": "O",
            "format": format,
            "etag": etag,
            "content_type": ct,
            "note": "Official original text; mirrored locally for reliability"
        }


# Example usage / smoke test
if __name__ == "__main__":
    api = NormattivaAPI()
    print("Fetching catalogue…")
    cat = api.get_collection_catalogue()
    print(f"Found {len(cat)} variants across {len(set(c['nomeCollezione'] for c in cat))} collections")
    print("\nFetching formats…")
    fmt = api.get_extensions()
    print(f"Available formats: {[f['label'] for f in fmt]}")
    print("\nGetting ETag for Codici…")
    etag = api.check_collection_etag("Codici", variant="O", format="AKN")
    print(f"Codici ETag: {etag}")
