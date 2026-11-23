"""
搜索商品数据解析器单元测试

测试项：
1. 真实形态 mtop 原始数据完整解析（image/link/want_cnt/seller_id/seller_avatar/seller_url）
2. http 图片 URL 归一化为 https
3. seller_id 优先使用 clickParam.args.seller_id，缺失时回落至 jump2XianYuHao.clickParam.args.user_id
4. 字段缺失时安全回落为空字符串/0，不发生异常崩溃
"""
from __future__ import annotations

import unittest
from app.services.search.parser import ItemParser


FIXTURE_REAL = {
    "data": {
        "item": {
            "main": {
                "exContent": {
                    "title": "斜插式笔筒",
                    "price": [{"text": "¥"}, {"text": "7.60"}],
                    "picUrl": "http://img.alicdn.com/bao/uploaded/i1/x.jpg",
                    "userNickName": "向阳而生逆光而行",
                    "userAvatarUrl": "http://img.alicdn.com/bao/uploaded/i1/avatar.jpg",
                    "area": "安徽",
                    "fishTags": {"r2": {"tagList": [{"data": {"content": "1155人想要"}}]}},
                },
                "jump2XianYuHao": {
                    "clickParam": {"args": {"user_id": "94q2JmFztJXsruwPDOKjZA=="}}
                },
                "targetUrl": "fleamarket://item?id=1001702399033",
                "clickParam": {
                    "args": {
                        "item_id": "1001702399033",
                        "seller_id": "8oExhNva/M408AtuBy0acg==",
                        "publishTime": "1765639970000",
                    }
                },
            }
        }
    }
}


class TestSearchParser(unittest.IsolatedAsyncioTestCase):
    """搜索解析器单元测试"""

    def setUp(self) -> None:
        self.parser = ItemParser()

    async def test_parse_item_full_fixture(self) -> None:
        """测试完整真实 mtop 数据的解析"""
        parsed = await self.parser.parse_item(FIXTURE_REAL)
        self.assertIsNotNone(parsed)

        # 核心字段断言
        self.assertEqual(parsed["item_id"], "1001702399033")
        self.assertEqual(parsed["title"], "斜插式笔筒")
        self.assertEqual(parsed["price"], "¥7.60")
        self.assertEqual(parsed["seller_name"], "向阳而生逆光而行")
        self.assertEqual(parsed["area"], "安徽")
        self.assertEqual(parsed["want_count"], 1155)

        # 新增及兼容字段断言
        self.assertEqual(parsed["seller_id"], "8oExhNva/M408AtuBy0acg==")
        self.assertEqual(parsed["seller_avatar"], "https://img.alicdn.com/bao/uploaded/i1/avatar.jpg")
        self.assertEqual(
            parsed["seller_url"],
            "https://www.goofish.com/personal?userId=8oExhNva%2FM408AtuBy0acg%3D%3D",
        )
        self.assertEqual(parsed["main_image"], "https://img.alicdn.com/bao/uploaded/i1/x.jpg")
        self.assertEqual(parsed["image"], "https://img.alicdn.com/bao/uploaded/i1/x.jpg")
        self.assertEqual(
            parsed["item_url"],
            "https://www.goofish.com/item?id=1001702399033",
        )
        self.assertEqual(
            parsed["link"],
            "https://www.goofish.com/item?id=1001702399033",
        )
        self.assertEqual(parsed["want_cnt"], 1155)
        self.assertEqual(parsed["tags"], ["1155人想要"])

    async def test_seller_id_fallback_to_jump2xianyuhao(self) -> None:
        """当 clickParam.args.seller_id 缺失时，回落到 jump2XianYuHao.clickParam.args.user_id"""
        data = {
            "data": {
                "item": {
                    "main": {
                        "exContent": {
                            "title": "测试笔筒",
                            "userAvatarUrl": "http://img.alicdn.com/avatar.png",
                        },
                        "jump2XianYuHao": {
                            "clickParam": {"args": {"user_id": "94q2JmFztJXsruwPDOKjZA=="}}
                        },
                        "clickParam": {
                            "args": {
                                "item_id": "123456",
                            }
                        },
                    }
                }
            }
        }
        parsed = await self.parser.parse_item(data)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["seller_id"], "94q2JmFztJXsruwPDOKjZA==")
        self.assertEqual(
            parsed["seller_url"],
            "https://www.goofish.com/personal?userId=94q2JmFztJXsruwPDOKjZA%3D%3D",
        )

    async def test_image_url_normalization(self) -> None:
        """测试图片 URL http 转 https 与各种格式归一化"""
        self.assertEqual(
            self.parser._normalize_image_url("http://img.alicdn.com/test.jpg"),
            "https://img.alicdn.com/test.jpg",
        )
        self.assertEqual(
            self.parser._normalize_image_url("https://img.alicdn.com/test.jpg"),
            "https://img.alicdn.com/test.jpg",
        )
        self.assertEqual(
            self.parser._normalize_image_url("//img.alicdn.com/test.jpg"),
            "https://img.alicdn.com/test.jpg",
        )
        self.assertEqual(self.parser._normalize_image_url(""), "")
        self.assertEqual(self.parser._normalize_image_url(None), "")

    async def test_missing_fields_graceful_fallback(self) -> None:
        """测试当各种字段完全缺失时安全回退，不崩溃"""
        parsed = await self.parser.parse_item({})
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["seller_id"], "")
        self.assertEqual(parsed["seller_avatar"], "")
        self.assertEqual(parsed["seller_url"], "")
        self.assertEqual(parsed["image"], "")
        self.assertEqual(parsed["main_image"], "")
        self.assertEqual(parsed["link"], "")
        self.assertEqual(parsed["item_url"], "")
        self.assertEqual(parsed["want_cnt"], 0)
        self.assertEqual(parsed["want_count"], 0)


if __name__ == "__main__":
    unittest.main()
