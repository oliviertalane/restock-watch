import json

import pytest

from restock_watch import platforms
from restock_watch.platforms import crystalcommerce, ecwid, jsonld, lightspeed, shopify
from restock_watch.platforms._common import price_from_text


class Response:
    def __init__(self, body):
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)


class Client:
    """Answers GETs from a {url-prefix: body} table; unknown URLs raise."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        for prefix, body in self.pages.items():
            if url.startswith(prefix):
                return Response(body(params) if callable(body) else body)
        raise ConnectionError(f"404 {url}")


@pytest.mark.parametrize("text,expected", [
    ("$1,299.99", "1299.99"), ("1 299,99 $", "1299.99"), ("CAD 54.99", "54.99"),
    ("54,99 €", "54.99"), ("1,299", "1299.00"), ("12", "12.00"), ("Free!", None), (None, None),
])
def test_price_from_text(text, expected):
    assert price_from_text(text) == expected


@pytest.mark.parametrize("url,kind,json_url", [
    ("https://s.example/collections/audio?sort=new#x", "list", "https://s.example/collections/audio/products.json"),
    ("https://s.example/collections/audio/products/item-1", "product", "https://s.example/products/item-1.js"),
    ("https://s.example/products/item-1.json", "product", "https://s.example/products/item-1.js"),
    ("https://s.example/search?q=box", "newest", "https://s.example/products.json"),
    ("https://s.example/", "list", "https://s.example/products.json"),
])
def test_shopify_plan(url, kind, json_url):
    assert shopify.plan(url)[:2] == (kind, json_url)


def shopify_item(i, available=True, price="10.00"):
    return {"id": i, "title": f"Item {i}", "handle": f"item-{i}", "images": [{"src": "//cdn.example/i.jpg"}],
            "variants": [{"available": False, "price": "99.00"}, {"available": available, "price": price}]}


def test_shopify_collection_reads_every_page():
    pages = {1: [shopify_item(i) for i in range(250)], 2: [shopify_item(250, available=False)]}
    client = Client({"https://s.example/collections/c/products.json": lambda p: {"products": pages[p["page"]]}})
    result = shopify.fetch(client, "https://s.example/collections/c")
    assert len(result.products) == 251 and result.ok and result.exhaustive
    first, last = result.products[0], result.products[-1]
    assert first.key == "shopify:s.example:0" and first.available and first.price == "10.00"
    assert first.image == "https://cdn.example/i.jpg" and first.url == "https://s.example/products/item-0"
    assert not last.available and last.price == "10.00"      # lowest price when nothing is in stock


def test_shopify_partial_read_keeps_products_but_is_not_ok():
    def page(params):
        if params["page"] == 2:
            raise ConnectionError("reset")
        return {"products": [shopify_item(i) for i in range(250)]}

    class Flaky(Client):
        def get(self, url, params=None):
            return Response(page(params))
    result = shopify.fetch(Flaky({}), "https://s.example/collections/c")
    assert len(result.products) == 250 and not result.ok and not result.exhaustive


def test_shopify_single_product():
    client = Client({"https://www.s.example/products/x.js": {
        "id": 7, "title": "X", "url": "/products/x", "available": True, "price": 179999,
        "featured_image": "//cdn.example/x.jpg", "variants": []}})
    (p,) = shopify.fetch(client, "https://www.s.example/products/x").products
    assert (p.key, p.price, p.available, p.image) == ("shopify:s.example:7", "1799.99", True,
                                                      "https://cdn.example/x.jpg")


CRYSTAL = """<html><head><link href="https://crystalcommerce.example/x.css"></head><body><ul>
<li class="product"><!-- if no variant is in stock then the product is out of stock -->
  <a href="/catalog/x/kettle/111"><img src="/img/1.jpg"></a><h4>Kettle</h4>
  <span class="price">CAD$ 54.99</span><div class="variant-row in-stock">Add to cart</div></li>
<li class="product"><a href="/catalog/x/tin/222"></a><h4>Tin</h4>
  <span class="price">19,99 $</span><div class="variant-row no-stock">Hors stock</div></li>
</ul></body></html>"""


def test_crystalcommerce():
    assert crystalcommerce.recognise(None, "", CRYSTAL)
    box, tin = crystalcommerce.parse(CRYSTAL, "https://cc.example/catalog/x/1")
    assert (box.title, box.available, box.price) == ("Kettle", True, "54.99")
    assert box.key == "crystalcommerce:cc.example/catalog/x/kettle/111"
    assert box.image == "https://cc.example/img/1.jpg"
    assert (tin.available, tin.price) == (False, "19.99")


def test_crystalcommerce_follows_pager(monkeypatch):
    monkeypatch.setattr(crystalcommerce, "PAGE_PAUSE_SECONDS", 0)
    tile = '<li class="product"><a href="/p/{0}"></a><h4>Item {0}</h4><em>1 In Stock</em></li>'
    page1 = ("<ul>" + tile.format(1) + tile.format(2) + "</ul>"
             '<div class="pagination"><span class="current">1</span>'
             '<a class="next_page" rel="next" href="/catalog/c/9?page=2">Next</a></div>')
    page2 = "<ul>" + tile.format(2) + tile.format(3) + '</ul><div class="pagination"><span class="current">2</span></div>'
    client = Client({"https://cc.example/catalog/c/9?page=2": page2, "https://cc.example/catalog/c/9": page1})
    result = crystalcommerce.fetch(client, "https://cc.example/catalog/c/9")
    assert [p.title for p in result.products] == ["Item 1", "Item 2", "Item 3"]
    assert result.exhaustive and all(p.available for p in result.products)

    capped = Client({"https://cc.example/catalog/c/9": page1.replace("?page=2", "")})    # endless "next"
    assert not crystalcommerce.fetch(capped, "https://cc.example/catalog/c/9").exhaustive


ECWID = """<html id="ecwid_html"><body>
<div class="grid-product grid-product--id-501"><a class="grid-product__image" href="/p/501"><img src="https://img.example/1.jpg"></a>
  <div class="grid-product__title-inner">Lamp</div><div class="grid-product__price-value">$12.00</div></div>
<div class="grid-product grid-product--id-502 ec-store-productsGrid-cell-outOfStock"><a href="/p/502"></a>
  <div class="grid-product__title-inner">Box</div></div></body></html>"""


def test_ecwid():
    assert ecwid.recognise(None, "", ECWID)
    lamp, box = ecwid.parse(ECWID, "https://e.example/shop")
    assert (lamp.key, lamp.available, lamp.price) == ("ecwid:e.example:501", True, "12.00")
    assert not box.available


def test_lightspeed_listing_and_product():
    listing = {"collection": {"pages": 1, "products": {"1": {
        "id": 1, "title": "Desk Lamp", "fulltitle": "Brand Desk Lamp", "url": "desk-lamp.html",
        "available": False, "price": {"price_incl": 74.99}}}}}
    product = {"product": {"id": 1, "title": "Desk Lamp", "fulltitle": "Brand Desk Lamp",
                           "url": "desk-lamp.html", "available": None, "stock": {"available": True}}}
    client = Client({"https://l.example/en/lighting/?": listing, "https://l.example/en/desk-lamp.html?": product})
    (p,) = lightspeed.fetch(client, "https://l.example/en/lighting/").products
    assert (p.title, p.url, p.available, p.price) == ("Brand Desk Lamp", "https://l.example/en/desk-lamp.html",
                                                      False, "74.99")
    (q,) = lightspeed.fetch(client, "https://l.example/en/desk-lamp.html").products
    assert q.key == p.key and q.available
    assert "format=json" in client.calls[0][0] and "limit=100" in client.calls[0][0]


def ld(data):
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


def test_jsonld_variants_graph_and_states():
    html = "<html>" + ld({"@context": "https://schema.org", "@graph": [
        {"@type": "WebPage"},
        {"@type": "ProductGroup", "name": "Shirt", "hasVariant": [
            {"@type": "Product", "name": "Shirt S", "sku": "S1",
             "offers": {"@type": "Offer", "price": "20.00", "availability": "https://schema.org/InStock"}},
            {"@type": "Product", "name": "Shirt M", "sku": "M1",
             "offers": [{"@type": "Offer", "price": 20, "availability": "http://schema.org/OutOfStock"}]}]},
    ]}) + ld({"@type": "Product", "name": "Box", "image": ["//cdn.example/b.jpg"],
              "offers": {"@type": "AggregateOffer", "lowPrice": "5",
                         "offers": [{"availability": "PreOrder"}]}}) + ld({"@type": "Product", "name": "No stock info"}) + "</html>"
    products = {p.title: p for p in jsonld.parse(html, "https://j.example/p")}
    assert set(products) == {"Shirt S", "Shirt M", "Box"}
    assert products["Shirt S"].available and not products["Shirt M"].available
    assert products["Shirt S"].key == "jsonld:j.example:S1"
    assert products["Box"].available and products["Box"].price == "5.00"
    assert products["Box"].image == "https://cdn.example/b.jpg"


def test_jsonld_price_specification_and_duplicate_blocks():
    html = (ld({"@graph": [{"@type": "Product", "name": "Case", "sku": "C1", "offers": {
                "availability": "https://schema.org/OutOfStock",
                "priceSpecification": [{"@type": "UnitPriceSpecification", "price": 1799.99}]}}]})
            + ld({"@type": "Product", "name": "Case", "sku": "C1", "image": "https://cdn.example/c.jpg",
                  "offers": {"price": 1799.99, "availability": "http://schema.org/OutOfStock"}}))
    (case,) = jsonld.parse(html, "https://j.example/p")
    assert (case.price, case.image, case.available) == ("1799.99", "https://cdn.example/c.jpg", False)


def test_detect_order():
    shop = Client({"https://s.example/products.json": {"products": []}})
    assert platforms.detect(shop, "https://s.example/collections/x") == "shopify"
    cc = Client({"https://c.example/catalog": CRYSTAL})
    assert platforms.detect(cc, "https://c.example/catalog/1") == "crystalcommerce"
    plain = Client({"https://g.example/": "<html><div class='product-card'><h3>A</h3></div></html>"})
    assert platforms.detect(plain, "https://g.example/list") == "generic"
