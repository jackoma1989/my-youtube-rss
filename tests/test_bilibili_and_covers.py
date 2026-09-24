import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from podcast_sync.bilibili_fetcher import BilibiliFetcher
from podcast_sync.config import Config
from podcast_sync.storage import StorageManager
from podcast_sync.youtube import YouTubeFetcher
from run_bilibili_sync import slugify_name


class TestBilibiliAndCovers(unittest.TestCase):

    def test_slugify_name(self):
        self.assertEqual(slugify_name("电影最TOP"), "dianyingzuitop")
        self.assertEqual(slugify_name("影视飓风"), "yingshijufeng")
        self.assertEqual(slugify_name("Geekerwan"), "geekerwan")
        self.assertEqual(slugify_name("Wang Zhian"), "wangzhian")

    def test_extract_mid(self):
        fetcher = BilibiliFetcher()
        self.assertEqual(fetcher.extract_mid("17819768"), "17819768")
        self.assertEqual(fetcher.extract_mid("https://space.bilibili.com/17819768"), "17819768")
        self.assertEqual(fetcher.extract_mid("https://space.bilibili.com/946974?spm_id_from=333.1007.0.0"), "946974")

    @patch("boto3.client")
    def test_storage_cover_exists(self, mock_boto):
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3
        cfg = Config(
            r2_account_id="acc",
            r2_access_key_id="key",
            r2_secret_access_key="secret",
            r2_bucket_name="testbucket",
            r2_public_url="https://podcast.hemajia.fun",
        )
        storage = StorageManager(cfg)
        
        # head_object succeeds
        mock_s3.head_object.return_value = {}
        self.assertTrue(storage.cover_exists("dianyingzuitop"))
        mock_s3.head_object.assert_called_with(Bucket="testbucket", Key="covers/dianyingzuitop.jpg")

    def test_download_image_bilibili_mocked(self):
        fetcher = BilibiliFetcher()
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b"fake_jpeg_content_that_is_long_enough_to_pass_check" * 20
            mock_get.return_value = mock_resp

            target = Path("output/test_cover.jpg")
            try:
                res = fetcher.download_image("https://i0.hdslb.com/bfs/face/test.jpg", target)
                self.assertTrue(res)
                self.assertTrue(target.exists())
            finally:
                target.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
