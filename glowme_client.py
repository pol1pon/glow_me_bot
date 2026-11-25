from __future__ import annotations

import logging
import re
import ssl
from dataclasses import dataclass
from typing import Iterable

import certifi
import httpx

logger = logging.getLogger(__name__)

TILDA_PRODUCTS_API = "https://store.tildaapi.com/api/getproductslist/"
DEFAULT_GLOWME_URL = "https://glowme.ru/"
DEFAULT_STOREPART_UID = "893754465082"
DEFAULT_RECID = "747166978"

_STOREPART_RE = re.compile(r"recid:\s*'(\d+)'\s*,\s*storepart:\s*'([^']+)'")
_UID_RE = re.compile(r"(?<!\d)(\d{9,15})(?!\d)")


@dataclass(frozen=True, slots=True)
class Product:
    """Товар каталога glowme.ru."""

    uid: str
    title: str
    url: str
    price: float | None
    quantity: int | None
    in_stock: bool
 
    @property
    def stock_label(self) -> str:
        if not self.in_stock:
            return "❌ Нет в наличии"
        if self.quantity is not None:
            return f"✅ В наличии ({self.quantity} шт.)"
        return "✅ В наличии"

    @property
    def price_label(self) -> str:
        if self.price is None:
            return "—"
        if self.price.is_integer():
            return f"{int(self.price):,} ₽".replace(",", " ")
        return f"{self.price:,.2f} ₽".replace(",", " ")


def _parse_int_quantity(raw: object) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    try:
        return max(0, int(float(text)))
    except ValueError:
        return None


def _parse_price(raw: object) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(" ", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_product(item: dict) -> Product:
    """Преобразует словарь товара из Tilda Store API в Product."""
    uid = str(item.get("uid", "")).strip()
    title = str(item.get("title", "")).strip() or f"Товар {uid}"
    url = str(item.get("url", "")).strip() or DEFAULT_GLOWME_URL
    price = _parse_price(item.get("price"))

    editions = item.get("editions") or []
    if isinstance(editions, list) and editions:
        ed_quantities = [_parse_int_quantity(ed.get("quantity")) for ed in editions]
        if any(q is None for q in ed_quantities):
            in_stock = True
            numeric = [q for q in ed_quantities if q is not None]
            quantity = sum(numeric) if numeric else None
        else:
            total_qty = sum(q for q in ed_quantities if q is not None)
            quantity = total_qty
            in_stock = total_qty > 0
    else:
        quantity = _parse_int_quantity(item.get("quantity"))
        in_stock = quantity is None or quantity > 0

    return Product(
        uid=uid,
        title=title,
        url=url,
        price=price,
        quantity=quantity,
        in_stock=in_stock,
    )


def find_products(products: Iterable[Product], query: str, limit: int = 10) -> list[Product]:
    """Ищет товары по ссылке glowme.ru, UID или названию."""
    raw = query.strip()
    if not raw:
        return []

    product_list = list(products)
    uids_in_query = _UID_RE.findall(raw)
    if uids_in_query:
        uid_matches = [p for p in product_list if p.uid in uids_in_query]
        if uid_matches:
            return uid_matches[:limit]

    normalized = raw.lower()
    exact_or_sub = [p for p in product_list if normalized in p.title.lower()]
    if exact_or_sub:
        return exact_or_sub[:limit]

    words = [w for w in normalized.split() if w]
    if len(words) > 1:
        word_matches = [
            p for p in product_list if all(word in p.title.lower() for word in words)
        ]
        return word_matches[:limit]

    return []


class GlowMeClient:
    """Асинхронный клиент для получения каталога и наличия товаров с glowme.ru."""

    def __init__(
        self,
        site_url: str = DEFAULT_GLOWME_URL,
        storepart_uid: str = DEFAULT_STOREPART_UID,
        recid: str = DEFAULT_RECID,
        timeout: float = 20.0,
    ) -> None:
        self.site_url = site_url
        self.storepart_uid = storepart_uid
        self.recid = recid
        self.timeout = timeout
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }

    async def discover_store_config(self, client: httpx.AsyncClient) -> tuple[str, str]:
        """Определяет storepart_uid и recid со страницы сайта (если структура обновилась)."""
        resp = await client.get(self.site_url, follow_redirects=True)
        resp.raise_for_status()
        for recid, storepart in _STOREPART_RE.findall(resp.text):
            if storepart:
                return storepart, recid
        raise RuntimeError("Не удалось найти параметры каталога Tilda на странице сайта.")

    async def _fetch_slice(
        self,
        client: httpx.AsyncClient,
        storepart_uid: str,
        recid: str,
        slice_num: int,
    ) -> dict:
        params = {
            "storepartuid": storepart_uid,
            "recid": recid,
            "getparts": "true",
            "getoptions": "true",
            "slice": str(slice_num),
            "size": "100",
        }
        resp = await client.get(TILDA_PRODUCTS_API, params=params)
        resp.raise_for_status()
        return resp.json()

    async def fetch_products(self) -> list[Product]:
        """Загружает все товары каталога glowme.ru."""
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers=self._headers,
            verify=ssl_context,
        ) as client:
            products_by_uid: dict[str, Product] = {}
            slice_num: int | None = 1
            refreshed_config = False

            while slice_num is not None:
                data = await self._fetch_slice(
                    client, self.storepart_uid, self.recid, slice_num
                )
                raw_products = data.get("products") or []

                if slice_num == 1 and not raw_products and not refreshed_config:
                    refreshed_config = True
                    try:
                        new_part, new_recid = await self.discover_store_config(client)
                        if (new_part, new_recid) != (self.storepart_uid, self.recid):
                            logger.info(
                                "Обновлены параметры каталога: storepart=%s, recid=%s",
                                new_part,
                                new_recid,
                            )
                            self.storepart_uid, self.recid = new_part, new_recid
                            continue
                    except Exception as exc:
                        logger.warning("Не удалось обновить параметры каталога: %s", exc)

                for item in raw_products:
                    product = parse_product(item)
                    if product.uid:
                        products_by_uid[product.uid] = product

                next_slice = data.get("nextslice")
                slice_num = int(next_slice) if next_slice else None

            return list(products_by_uid.values())
