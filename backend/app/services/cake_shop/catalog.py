"""服务端权威蛋糕店商品目录。"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CakeProduct:
    product_id: str
    name: str
    description: str
    price_fen: int
    image_key: str

    def as_dict(self) -> dict:
        return {**asdict(self), "price": self.price_fen / 100, "currency": "CNY"}


_PRODUCTS = (
    CakeProduct(
        product_id="STRAWBERRY_CREAM",
        name="草莓鲜奶蛋糕",
        description="6 英寸轻盈奶油与新鲜草莓，适合 2–4 人分享。",
        price_fen=9900,
        image_key="strawberry",
    ),
    CakeProduct(
        product_id="CHOCOLATE_DELIGHT",
        name="浓醇巧克力蛋糕",
        description="8 英寸可可海绵与巧克力甘纳许，适合 4–6 人庆祝。",
        price_fen=19900,
        image_key="chocolate",
    ),
    CakeProduct(
        product_id="CELEBRATION_LAYERS",
        name="庆典双层蛋糕",
        description="双层香草奶油与莓果装饰，适合 12–16 人的特别庆典。",
        price_fen=129900,
        image_key="celebration",
    ),
)

PRODUCTS_BY_ID = {product.product_id: product for product in _PRODUCTS}


def list_products() -> list[dict]:
    return [product.as_dict() for product in _PRODUCTS]
