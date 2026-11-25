from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import evaluate_catalog_updates
from glowme_client import Product, find_products, parse_product
from storage import Storage


def test_parse_product_out_of_stock_and_in_stock():
    p_out = parse_product(
        {
            "uid": 159767821973,
            "title": "АДВЕНТ-КАЛЕНДАРЬ",
            "price": "11250.0000",
            "quantity": "0",
            "url": "https://glowme.ru/catalog/tproduct/159767821973-advent-kalendar",
            "editions": [{"uid": 159767821973, "quantity": "0"}],
        }
    )
    assert p_out.uid == "159767821973"
    assert p_out.in_stock is False
    assert p_out.quantity == 0
    assert p_out.price_label == "11 250 ₽"

    p_in = parse_product(
        {
            "uid": 385859747483,
            "title": "НАБОР ГЕЛЬ-МАСЕЛ",
            "price": "2399.0000",
            "quantity": "141",
            "url": "https://glowme.ru/catalog/tproduct/385859747483-nabor",
            "editions": [{"uid": 385859747483, "quantity": "141"}],
        }
    )
    assert p_in.in_stock is True
    assert p_in.quantity == 141
    assert p_in.stock_label == "✅ В наличии (141 шт.)"


def test_parse_product_unlimited_and_multi_editions():
    p_unlim = parse_product(
        {
            "uid": 111222333444,
            "title": "Сертификат",
            "price": "3000",
            "quantity": "",
            "editions": [],
        }
    )
    assert p_unlim.in_stock is True
    assert p_unlim.quantity is None

    p_multi = parse_product(
        {
            "uid": 555666777888,
            "title": "Набор оттенков",
            "price": "1990",
            "quantity": "0",
            "editions": [
                {"uid": 1, "quantity": "0"},
                {"uid": 2, "quantity": "5"},
            ],
        }
    )
    assert p_multi.in_stock is True
    assert p_multi.quantity == 5


def test_find_products_by_url_and_title():
    products = [
        Product("159767821973", "АДВЕНТ-КАЛЕНДАРЬ", "https://glowme.ru/catalog/tproduct/159767821973-advent-kalendar", 11250, 0, False),
        Product("340818551053", "Масло-блеск HOT HONEY", "https://glowme.ru/catalog/tproduct/340818551053-maslo-blesk-hot-honey", 990, 0, False),
        Product("633851086442", "Масло-блеск BERRY", "https://glowme.ru/catalog/tproduct/633851086442-maslo-blesk-berry", 990, 10, True),
    ]

    by_url = find_products(
        products, "https://glowme.ru/catalog/tproduct/159767821973-advent-kalendar"
    )
    assert [p.uid for p in by_url] == ["159767821973"]

    by_title = find_products(products, "масло")
    assert len(by_title) == 2

    by_words = find_products(products, "масло honey")
    assert [p.uid for p in by_words] == ["340818551053"]


def test_evaluate_catalog_updates_subscription_and_watch_all(tmp_path):
    storage = Storage(tmp_path / "test.db")
    p1_out = Product("100000000001", "Товар 1", "https://glowme.ru/p1", 1000, 0, False)
    p2_in = Product("100000000002", "Товар 2", "https://glowme.ru/p2", 2000, 10, True)

    storage.add_subscription(111, p1_out.uid, p1_out.title, p1_out.url, p1_out.in_stock)
    storage.set_watch_all(222, True)

    # Первый проход — только сохраняет снимок каталога, без спама для watch_all
    events_first = evaluate_catalog_updates([p1_out, p2_in], storage)
    assert events_first == []

    # Товар 1 появился в наличии, Товар 3 — новая позиция в наличии
    p1_in = Product("100000000001", "Товар 1", "https://glowme.ru/p1", 1000, 5, True)
    p3_new = Product("100000000003", "Товар 3", "https://glowme.ru/p3", 1500, 3, True)
    events_second = evaluate_catalog_updates([p1_in, p2_in, p3_new], storage)

    assert {(e.chat_id, e.product.uid, e.reason) for e in events_second} == {
        (111, "100000000001", "subscription"),
        (222, "100000000001", "watch_all_restock"),
        (222, "100000000003", "watch_all_new"),
    }

    # Повторный проход без изменений — новых уведомлений нет
    events_third = evaluate_catalog_updates([p1_in, p2_in, p3_new], storage)
    assert events_third == []
