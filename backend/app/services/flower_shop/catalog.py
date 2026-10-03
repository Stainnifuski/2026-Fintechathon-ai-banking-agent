"""服务端权威花店商品目录。"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FlowerProduct:
    product_id: str
    name: str
    description: str
    price_fen: int
    image_key: str

    def as_dict(self) -> dict:
        return {**asdict(self), "price": self.price_fen / 100, "currency": "CNY"}


_PRODUCTS = (
    FlowerProduct(
        product_id="ROSE_CLASSIC",
        name="经典红玫瑰",
        description="11 枝红玫瑰与尤加利叶，适合表达爱意。",
        price_fen=9900,
        image_key="rose",
    ),
    FlowerProduct(
        product_id="SUNSHINE",
        name="向日葵暖阳花束",
        description="向日葵与香槟玫瑰混搭，适合生日与祝福。",
        price_fen=19900,
        image_key="sunflower",
    ),
    FlowerProduct(
        product_id="PREMIUM_GARDEN",
        name="花园臻选礼盒",
        description="进口花材礼盒；用于演示大额付款与 TOTP 流程。",
        price_fen=129900,
        image_key="premium",
    ),
)

PRODUCTS_BY_ID = {product.product_id: product for product in _PRODUCTS}


def list_products() -> list[dict]:
    return [product.as_dict() for product in _PRODUCTS]
